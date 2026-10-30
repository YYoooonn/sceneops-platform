"""The registration gate for payload references (ADR-017):
``verify_recording_payload_refs`` / ``verify_content_payload_refs`` over a
real local store, with recording payloads published as the recording builders
publish them and contents published by ``publish_snapshot``.

The manifests are builder output: synthetic recording Scenes / Episodes from
the test helpers, and the real nuScenes and LeRobot builders over synthetic
trees. The registrars' use of the gate is covered by
``test_registration_payload_gate.py``."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
    PayloadRef,
    VideoFrameSelector,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.config import ArtifactSettings
from sceneops_core.episodes import testing as episode_testing
from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig
from sceneops_core.episodes.schemas import EPISODE_MANIFEST_SCHEMA_V4
from sceneops_core.provenance import PinnedContent
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_core.scenes import testing as scene_testing
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.scenes.schemas import SCENE_MANIFEST_SCHEMA_V5
from sceneops_core.sources import (
    SnapshotDeclaration,
    SnapshotSelection,
    SourceContentRecord,
)
from sceneops_episodes.lerobot_builder import plan_lerobot_episodes
from sceneops_recording.observations.store import ObservationPayloadStore
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes
from sceneops_sources import (
    LocalContentFiles,
    content_file_uri,
    publish_snapshot,
)
from sceneops_sources import lerobot_synthetic, nuscenes_synthetic
from sceneops_storage import LocalArtifactStore
from sceneops_worker.payload_registration import (
    PayloadRegistrationError,
    verify_content_payload_refs,
    verify_recording_payload_refs,
)
from sceneops_worker.sources.content import resolve_registered_content

RUN = "run-001"
OTHER_RUN = "run-002"
NUSCENES_CONFIG = NuScenesSceneBuildConfig(channels=sorted(nuscenes_synthetic.CHANNELS))
LEROBOT_CONFIG = LeRobotEpisodeBuildConfig(
    features=[
        {"feature": "observation.image", "role": "observation"},
        {"feature": "observation.state", "role": "state"},
        {"feature": "action", "role": "action"},
    ]
)


class _Store(LocalArtifactStore):
    """The real local store, counting listings and refusing writes once
    ``frozen``: the verifiers read only."""

    def __init__(self, root_uri: str) -> None:
        super().__init__(root_uri=root_uri)
        self.listings: list[str] = []
        self.frozen = False

    async def list_objects(self, uri):
        self.listings.append(uri)
        return await super().list_objects(uri)

    async def write_bytes(self, uri, data):
        assert not self.frozen, f"a verifier wrote {uri}"
        return await super().write_bytes(uri, data)

    async def delete(self, uri) -> None:
        """Lose one stored object, as a test of a store that lost it."""
        self._to_path(uri).unlink()


class _Records:
    """ArtifactRecords by id; the verifiers have nothing to write here."""

    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}
        self.lookups: list[list[str]] = []

    async def get(self, artifact_id):
        self.lookups.append([artifact_id])
        return self.records.get(artifact_id)

    async def get_many(self, ids):
        self.lookups.append(list(ids))
        return {i: self.records[i] for i in ids if i in self.records}


class _Sources:
    def __init__(self) -> None:
        self.contents: dict[str, SourceContentRecord] = {}

    async def get_content(self, content_id):
        return self.contents.get(content_id)


def _run(run_id: str = RUN) -> RobotRunRecord:
    return RobotRunRecord(
        run_id=run_id,
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=f"art-recording-{run_id}",
        manifest_artifact_id=f"art-robotrunmanifest-{run_id}",
        manifest_checksum="sha256:" + "1" * 64,
    )


class _World:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.root = str(tmp_path / "artifacts")
        self.settings = ArtifactSettings(root_uri=self.root)
        self.store = _Store(self.root)
        self.records = _Records()
        self.sources = _Sources()
        # Exactly what the verifiers may use: no unit, revision or
        # DatasetVersion store exists here to be written.
        self.context = SimpleNamespace(
            settings=SimpleNamespace(artifact=self.settings),
            artifact_store=self.store,
            artifact_record_store=self.records,
            source_store=self.sources,
        )
        self.payloads = ObservationPayloadStore(
            artifact_store=self.store,
            payload_root_uri=self.settings.observation_payload_root_uri,
        )

    # -- recording payloads, as the recording builders publish them ------------

    async def publish_payload(
        self,
        artifact_id: str,
        data: bytes,
        pinned_media_type: str,
        *,
        run_id: str = RUN,
        record: bool = True,
        **record_fields,
    ) -> PayloadRef:
        uri, _ = await self.payloads.publish(
            robot_run_id=run_id,
            artifact_id=artifact_id,
            data=data,
            checksum=sha256_checksum(data),
        )
        if record:
            self.records.records[artifact_id] = ArtifactRecord(
                **{
                    "artifact_id": artifact_id,
                    "kind": ArtifactKind.OBSERVATION_PAYLOAD,
                    "uri": uri,
                    "media_type": pinned_media_type,
                    "size_bytes": len(data),
                    "checksum": sha256_checksum(data),
                    "owner_type": ArtifactOwnerType.ROBOT_RUN,
                    "owner_id": run_id,
                    **record_fields,
                }
            )
        return PayloadRef(
            uri=format_logical_uri(
                LogicalNamespace.OBSERVATION_PAYLOADS, f"{run_id}/{artifact_id}"
            ),
            checksum=sha256_checksum(data),
            size_bytes=len(data),
            media_type=pinned_media_type,
        )

    async def recording_scene(self):
        """A recording Scene v5 of RUN whose payloads are published."""
        manifest = scene_testing.build_scene_manifest(
            source=scene_testing.recording_source(robot_run_id=RUN)
        )
        for observation in manifest.observations:
            await self.publish_payload(
                _artifact_id(observation.payload),
                scene_testing.payload_bytes(observation.observation_id),
                observation.payload.media_type,
            )
        return manifest

    async def recording_episode(self):
        """A recording Episode v4 of RUN whose payloads are published."""
        manifest = episode_testing.episode_manifest(
            [
                episode_testing.observation("/cam", 0),
                episode_testing.observation("/cam", 500_000_000),
                episode_testing.state("/odom", 1_000_000_000, x=1.0),
            ],
            robot_run_id=RUN,
            window=(0, 2_000_000_000),
        )
        for occurrence in manifest.occurrences():
            if occurrence.payload is None:
                continue
            rank = int(occurrence.occurrence_id.rsplit("-", 1)[1])
            await self.publish_payload(
                _artifact_id(occurrence.payload),
                episode_testing.payload_bytes(occurrence.topic, rank),
                occurrence.payload.media_type,
            )
        return manifest

    # -- preserved contents ----------------------------------------------------

    async def content(self, tree, fmt: str, include, *, sources_root=None):
        """Publish ``tree`` and register its content, as REGISTER_SOURCE_SNAPSHOT
        leaves it; returns (RegisteredContent, PublishedSnapshot)."""
        published = await publish_snapshot(
            store=self.store,
            sources_root=sources_root or self.settings.source_root_uri,
            source_dir=tree,
            declaration=SnapshotDeclaration(
                format=fmt,
                upstream="test/payload-registration",
                upstream_revision=tree.name,
                selection=SnapshotSelection(include=tuple(include)),
            ),
        )
        inventory = published.inventory
        inventory_artifact_id = f"art-inventory-{inventory.content_id}"
        self.records.records[inventory_artifact_id] = ArtifactRecord(
            artifact_id=inventory_artifact_id,
            kind=ArtifactKind.SOURCE_CONTENT_INVENTORY,
            uri=published.inventory_uri,
        )
        self.sources.contents[inventory.content_id] = SourceContentRecord(
            content_id=inventory.content_id,
            format=fmt,
            content_checksum=inventory.content_checksum,
            inventory_artifact_id=inventory_artifact_id,
            file_count=len(inventory.files),
            total_bytes=sum(f.size_bytes for f in inventory.files),
        )
        registered = await resolve_registered_content(
            self.context, inventory.content_id
        )
        return registered, published

    async def nuscenes(self, name: str = "a", **kwargs):
        tree = nuscenes_synthetic.write_nuscenes_tree(
            self.tmp_path / f"nuscenes-{name}", scenes=2, samples=2, sweeps=1
        )
        (tree / "LICENSE").write_text(f"license {name}\n")
        return await self.content(
            tree, "nuscenes", nuscenes_synthetic.INCLUDE, **kwargs
        ), tree

    async def nuscenes_scenes(self, name: str = "a"):
        """Every Scene the nuScenes builder builds from a registered synthetic
        content; returns (content, [manifest])."""
        (content, published), tree = await self.nuscenes(name)
        plan = plan_nuscenes_scenes(
            pinned=PinnedContent(inventory=published.inventory),
            files=LocalContentFiles(tree, published.inventory),
            config=NUSCENES_CONFIG,
        )
        return content, [p.manifest for p in plan.scenes]

    async def lerobot_episodes(self):
        tree = lerobot_synthetic.write_lerobot_tree(self.tmp_path / "lerobot")
        content, published = await self.content(
            tree, "lerobot", lerobot_synthetic.INCLUDE
        )
        plan = plan_lerobot_episodes(
            pinned=PinnedContent(inventory=published.inventory),
            files=LocalContentFiles(tree, published.inventory),
            config=LEROBOT_CONFIG,
        )
        return content, [p.manifest for p in plan.episodes]

    async def objects(self) -> list[tuple[str, int]]:
        return [(o.uri, o.size_bytes) for o in await self.store.list_objects(self.root)]

    def start(self) -> None:
        """From here on the verifiers run: count their I/O, refuse writes."""
        self.store.listings.clear()
        self.records.lookups.clear()
        self.store.frozen = True


@pytest.fixture()
def world(tmp_path) -> _World:
    return _World(tmp_path)


def _artifact_id(ref: PayloadRef) -> str:
    """The payload artifact id a recording payload key names."""
    return ref.logical_uri().key.rpartition("/")[2]


def _file_path(content, ref: PayloadRef) -> str:
    """The inventory path a content file reference names."""
    prefix = f"contents/{content.record.content_id}/files/"
    key = ref.logical_uri().key
    assert key.startswith(prefix)
    return key[len(prefix) :]


def _scene_refs(manifest) -> list[PayloadRef]:
    return [o.payload for o in manifest.observations]


def _episode_refs(manifest) -> list[PayloadRef]:
    return [o.payload for o in manifest.occurrences() if o.payload is not None]


def _unchecked(ref: PayloadRef, **fields) -> PayloadRef:
    """``ref`` with ``fields`` set and no validation, as a caller that built a
    PayloadRef without the schema could hand it over."""
    return PayloadRef.model_construct(**{**ref.model_dump(), **fields})


# ── valid references ───────────────────────────────────────────────────────


async def test_a_recording_scene_v5_validates_against_its_records(world):
    manifest = await world.recording_scene()
    assert manifest.schema_version == SCENE_MANIFEST_SCHEMA_V5
    before = await world.objects()
    world.start()
    verified = await verify_recording_payload_refs(
        world.context, _run(), _scene_refs(manifest)
    )
    # Each URI resolves to exactly the object its record registered.
    assert verified == {
        ref.uri: world.records.records[_artifact_id(ref)].uri
        for ref in _scene_refs(manifest)
    }
    # One record lookup and one listing for the whole input; nothing written.
    assert len(world.records.lookups) == 1
    assert world.store.listings == [
        f"{world.settings.observation_payload_root_uri}/{RUN}"
    ]
    assert await world.objects() == before


async def test_a_recording_episode_v4_validates_against_its_records(world):
    manifest = await world.recording_episode()
    assert manifest.schema_version == EPISODE_MANIFEST_SCHEMA_V4
    world.start()
    verified = await verify_recording_payload_refs(
        world.context, _run(), _episode_refs(manifest)
    )
    assert sorted(verified.values()) == sorted(
        world.records.records[_artifact_id(ref)].uri for ref in _episode_refs(manifest)
    )


async def test_a_nuscenes_scene_v5_validates_against_its_inventory(world):
    content, scenes = await world.nuscenes_scenes()
    manifest = scenes[0]
    assert manifest.schema_version == SCENE_MANIFEST_SCHEMA_V5
    before = await world.objects()
    world.start()
    verified = await verify_content_payload_refs(
        world.context, content, _scene_refs(manifest)
    )
    # Each URI resolves to exactly where the inventory places the file.
    assert verified == {
        ref.uri: content_file_uri(content.inventory_uri, _file_path(content, ref))
        for ref in _scene_refs(manifest)
    }
    # The inventory was loaded once by the caller; the verifier lists once.
    assert world.records.lookups == []
    assert world.store.listings == [
        f"{world.settings.source_root_uri}/contents/{content.record.content_id}/files"
    ]
    assert await world.objects() == before


async def test_a_lerobot_episode_v4_keeps_its_frame_selectors(world):
    content, episodes = await world.lerobot_episodes()
    manifest = episodes[0]
    assert manifest.schema_version == EPISODE_MANIFEST_SCHEMA_V4
    framed = [r for r in _episode_refs(manifest) if r.selector is not None]
    assert framed and all(isinstance(r.selector, VideoFrameSelector) for r in framed)
    assert {r.uri for r in framed} == {
        format_logical_uri(
            LogicalNamespace.SOURCES,
            f"contents/{content.record.content_id}/files/{lerobot_synthetic.VIDEO_PATH}",
        )
    }
    world.start()
    verified = await verify_content_payload_refs(
        world.context, content, _episode_refs(manifest)
    )
    # Many frames of one video: one object, verified once, selectors untouched.
    assert verified == {
        framed[0].uri: content_file_uri(
            content.inventory_uri, lerobot_synthetic.VIDEO_PATH
        )
    }
    assert len(world.store.listings) == 1


async def test_a_batch_checks_each_object_once(world):
    """Every manifest of one build in one call, references repeated: one
    record lookup of the distinct ids and one listing."""
    scene = await world.recording_scene()
    episode = await world.recording_episode()
    refs = _scene_refs(scene) * 3 + _episode_refs(episode) * 2
    world.start()
    verified = await verify_recording_payload_refs(world.context, _run(), refs)
    assert len(verified) == len({r.uri for r in refs})
    (lookup,) = world.records.lookups
    assert sorted(lookup) == sorted(set(lookup)) and len(lookup) == len(verified)
    assert len(world.store.listings) == 1

    world.store.frozen = False
    content, scenes = await world.nuscenes_scenes()
    world.start()
    refs = [r for m in scenes for r in _scene_refs(m)] * 2
    await verify_content_payload_refs(world.context, content, refs)
    assert len(world.store.listings) == 1


async def test_no_reference_needs_no_lookup(world):
    world.start()
    assert await verify_recording_payload_refs(world.context, _run(), []) == {}
    assert world.records.lookups == [] and world.store.listings == []


# ── invalid: any reference ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("uri", "match"),
    [
        ("sceneops://datasets/run-001/payload-1", "unknown namespace"),
        ("s3://bucket/payloads/run-001/payload-1", "not a sceneops://"),
        ("sceneops://observation-payloads/run-001/payload%2D1", "not canonical"),
        ("sceneops://observation-payloads/run-001/payload%2d1", "percent-encoded"),
        ("sceneops://observation-payloads/run-001/..", "'..' segments"),
        ("sceneops://observation-payloads/run-001%2Fpayload-1", "encodes a '/'"),
    ],
)
async def test_an_invalid_or_non_canonical_uri_is_refused(world, uri, match):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    with pytest.raises(PayloadRegistrationError, match="not a valid PayloadRef") as exc:
        await verify_recording_payload_refs(
            world.context, _run(), [_unchecked(ref, uri=uri)]
        )
    assert match in str(exc.value)


async def test_a_selector_the_media_type_cannot_hold_is_refused(world):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    framed = _unchecked(ref, selector=VideoFrameSelector(timestamp_ns=1))
    with pytest.raises(PayloadRegistrationError, match="cannot select part"):
        await verify_recording_payload_refs(world.context, _run(), [framed])


async def test_a_uri_pinned_two_ways_is_refused(world):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    other = ref.model_copy(update={"media_type": "image/png"})
    with pytest.raises(PayloadRegistrationError, match="pinned inconsistently"):
        await verify_recording_payload_refs(world.context, _run(), [ref, other])


async def test_a_namespace_that_is_not_the_units_source_is_refused(world):
    (content, _), _ = await world.nuscenes()
    payload = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    file_ref = PayloadRef(
        uri=format_logical_uri(
            LogicalNamespace.SOURCES,
            f"contents/{content.record.content_id}/files/LICENSE",
        ),
        checksum=sha256_checksum(b"license a\n"),
        size_bytes=len(b"license a\n"),
        media_type="text/plain",
    )
    with pytest.raises(PayloadRegistrationError, match="lie in observation-payloads"):
        await verify_recording_payload_refs(world.context, _run(), [file_ref])
    with pytest.raises(PayloadRegistrationError, match="lie in sources"):
        await verify_content_payload_refs(world.context, content, [payload])


# ── invalid: recording payloads ────────────────────────────────────────────


async def test_a_payload_of_another_robot_run_is_refused(world):
    ref = await world.publish_payload(
        "payload-1", b"\x01\x02", "image/jpeg", run_id=OTHER_RUN
    )
    with pytest.raises(PayloadRegistrationError, match="RobotRun 'run-002'"):
        await verify_recording_payload_refs(world.context, _run(), [ref])
    # It is valid for its own run.
    await verify_recording_payload_refs(world.context, _run(OTHER_RUN), [ref])


async def test_a_key_that_is_not_a_payload_key_is_refused(world):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    for key in (RUN, f"{RUN}/nested/payload-1"):
        nested = _unchecked(
            ref, uri=format_logical_uri(LogicalNamespace.OBSERVATION_PAYLOADS, key)
        )
        with pytest.raises(PayloadRegistrationError, match="not a payload key"):
            await verify_recording_payload_refs(world.context, _run(), [nested])


async def test_an_empty_recording_payload_is_refused(world):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    empty = ref.model_copy(update={"size_bytes": 0, "checksum": sha256_checksum(b"")})
    with pytest.raises(PayloadRegistrationError, match="empty payload"):
        await verify_recording_payload_refs(world.context, _run(), [empty])


async def test_a_stored_payload_without_a_record_is_refused(world):
    ref = await world.publish_payload(
        "payload-1", b"\x01\x02", "image/jpeg", record=False
    )
    with pytest.raises(PayloadRegistrationError, match="is not registered"):
        await verify_recording_payload_refs(world.context, _run(), [ref])


@pytest.mark.parametrize(
    ("record_fields", "match"),
    [
        ({"kind": ArtifactKind.SCENE_MANIFEST}, "is a 'scene_manifest'"),
        ({"owner_type": ArtifactOwnerType.SCENE}, "is owned by 'scene'"),
        ({"owner_id": OTHER_RUN}, "'run-002', not by RobotRun 'run-001'"),
        ({"owner_id": None}, "is owned by"),
        ({"checksum": "sha256:" + "0" * 64}, "checksum"),
        ({"checksum": None}, "checksum"),
        ({"size_bytes": 3}, "size_bytes"),
        ({"media_type": "image/png"}, "media_type"),
    ],
)
async def test_a_record_that_does_not_back_the_reference_is_refused(
    world, record_fields, match
):
    ref = await world.publish_payload(
        "payload-1", b"\x01\x02", "image/jpeg", **record_fields
    )
    with pytest.raises(PayloadRegistrationError, match=match):
        await verify_recording_payload_refs(world.context, _run(), [ref])


async def test_a_reference_pinned_differently_from_its_record_is_refused(world):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    for update, match in (
        ({"checksum": "sha256:" + "0" * 64}, "checksum"),
        ({"size_bytes": 3}, "size_bytes"),
    ):
        with pytest.raises(PayloadRegistrationError, match=match):
            await verify_recording_payload_refs(
                world.context, _run(), [ref.model_copy(update=update)]
            )


async def test_a_record_of_another_object_is_refused(world):
    """The record found by the URI's artifact id must register the object the
    URI resolves to, not one stored elsewhere (physical URIs are not unique,
    so the record is never found by its URI)."""
    elsewhere = f"{world.root}/elsewhere/{RUN}/payload-1"
    ref = await world.publish_payload(
        "payload-1", b"\x01\x02", "image/jpeg", uri=elsewhere
    )
    with pytest.raises(PayloadRegistrationError, match=f"is stored at {elsewhere}"):
        await verify_recording_payload_refs(world.context, _run(), [ref])


async def test_a_record_spoofing_another_runs_object_is_refused(world):
    """A record of the unit's run pointing at another run's object, and a
    record of another run pointing at the unit's object, are both refused."""
    victim = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    victim_uri = world.records.records["payload-1"].uri
    world.records.records["payload-spoof"] = world.records.records[
        "payload-1"
    ].model_copy(update={"artifact_id": "payload-spoof", "owner_id": OTHER_RUN})
    spoof = victim.model_copy(
        update={
            "uri": format_logical_uri(
                LogicalNamespace.OBSERVATION_PAYLOADS, f"{RUN}/payload-spoof"
            )
        }
    )
    with pytest.raises(PayloadRegistrationError, match="not by RobotRun 'run-001'"):
        await verify_recording_payload_refs(world.context, _run(), [spoof])
    spoof = spoof.model_copy(
        update={
            "uri": format_logical_uri(
                LogicalNamespace.OBSERVATION_PAYLOADS, f"{OTHER_RUN}/payload-spoof"
            )
        }
    )
    with pytest.raises(PayloadRegistrationError, match=f"is stored at {victim_uri}"):
        await verify_recording_payload_refs(world.context, _run(OTHER_RUN), [spoof])


async def test_a_missing_or_resized_payload_object_is_refused(world):
    ref = await world.publish_payload("payload-1", b"\x01\x02", "image/jpeg")
    uri = world.records.records["payload-1"].uri
    await world.store.write_bytes(uri, b"\x01\x02\x03")
    with pytest.raises(
        PayloadRegistrationError, match="has 3 bytes; the reference pins 2"
    ):
        await verify_recording_payload_refs(world.context, _run(), [ref])
    await world.store.delete(uri)
    with pytest.raises(PayloadRegistrationError, match="has no object at"):
        await verify_recording_payload_refs(world.context, _run(), [ref])


# ── invalid: preserved content files ───────────────────────────────────────


def _file_ref(content, path: str, data: bytes, **fields) -> PayloadRef:
    return PayloadRef(
        **{
            "uri": format_logical_uri(
                LogicalNamespace.SOURCES,
                f"contents/{content.record.content_id}/files/{path}",
            ),
            "checksum": sha256_checksum(data),
            "size_bytes": len(data),
            "media_type": "text/plain",
            **fields,
        }
    )


async def test_a_file_of_another_content_is_refused_even_with_identical_bytes(world):
    (own, _), _ = await world.nuscenes("a")
    (other, _), _ = await world.nuscenes("b")
    path = next(f.path for f in own.inventory.files if f.path.endswith(".pcd.bin"))
    entry = next(f for f in other.inventory.files if f.path == path)
    assert entry in own.inventory.files  # the same bytes in both contents
    ref = PayloadRef(
        uri=format_logical_uri(
            LogicalNamespace.SOURCES,
            f"contents/{other.record.content_id}/files/{path}",
        ),
        checksum=entry.sha256,
        size_bytes=entry.size_bytes,
        media_type="application/x.nuscenes.lidar-pcd-bin",
    )
    with pytest.raises(
        PayloadRegistrationError,
        match=f"does not name a file of the unit's content {own.record.content_id}",
    ):
        await verify_content_payload_refs(world.context, own, [ref])
    await verify_content_payload_refs(world.context, other, [ref])


async def test_a_stored_file_missing_from_the_inventory_is_refused(world):
    (content, _), _ = await world.nuscenes()
    extra = content_file_uri(content.inventory_uri, "samples/extra.bin")
    await world.store.write_bytes(extra, b"extra")
    with pytest.raises(PayloadRegistrationError, match="is not a file of"):
        await verify_content_payload_refs(
            world.context, content, [_file_ref(content, "samples/extra.bin", b"extra")]
        )


@pytest.mark.parametrize(
    "fields",
    [{"checksum": "sha256:" + "0" * 64}, {"size_bytes": 99}],
)
async def test_a_file_pinned_differently_from_its_inventory_is_refused(world, fields):
    (content, _), _ = await world.nuscenes()
    ref = _file_ref(content, "LICENSE", b"license a\n", **fields)
    with pytest.raises(PayloadRegistrationError, match="the inventory pins"):
        await verify_content_payload_refs(world.context, content, [ref])


async def test_a_content_object_that_is_not_a_file_is_refused(world):
    (content, published), _ = await world.nuscenes()
    inventory_bytes = await world.store.read_bytes(published.inventory_uri)
    snapshot_key = f"snapshots/{published.snapshot_id}/descriptor.json"
    descriptor_bytes = await world.store.read_bytes(published.descriptor_uri)
    for key, data in (
        (f"contents/{content.record.content_id}/inventory.json", inventory_bytes),
        (snapshot_key, descriptor_bytes),
    ):
        ref = PayloadRef(
            uri=format_logical_uri(LogicalNamespace.SOURCES, key),
            checksum=sha256_checksum(data),
            size_bytes=len(data),
            media_type="application/json",
        )
        with pytest.raises(PayloadRegistrationError, match="does not name a file"):
            await verify_content_payload_refs(world.context, content, [ref])


@pytest.mark.parametrize(
    ("encoded_path", "match"),
    [
        ("..%2FLICENSE", "encodes a '/'"),
        ("samples/../LICENSE", "'..' segments"),
        ("samples/%2E%2E/LICENSE", "not canonical"),
        ("samples//LICENSE", "percent-encoded"),
        ("LICENS%45", "not canonical"),
        ("LICENSE%zz", "percent-encoded"),
        ("LICENSE%C3", "not UTF-8"),
    ],
)
async def test_a_traversing_or_malformed_path_is_refused(world, encoded_path, match):
    (content, _), _ = await world.nuscenes()
    ref = _unchecked(
        _file_ref(content, "LICENSE", b"license a\n"),
        uri=f"sceneops://sources/contents/{content.record.content_id}/files/"
        f"{encoded_path}",
    )
    with pytest.raises(PayloadRegistrationError, match="not a valid PayloadRef") as exc:
        await verify_content_payload_refs(world.context, content, [ref])
    assert match in str(exc.value)


async def test_a_verbatim_native_filename_round_trips(world):
    tree = world.tmp_path / "odd"
    name = "samples/a+b?c#d%e é.bin"
    (tree / "samples").mkdir(parents=True)
    (tree / name).write_bytes(b"odd bytes")
    content, _ = await world.content(tree, "nuscenes", ("**",))
    ref = _file_ref(content, name, b"odd bytes")
    assert ref.uri.endswith("/files/samples/a%2Bb%3Fc%23d%25e%20%C3%A9.bin")
    verified = await verify_content_payload_refs(world.context, content, [ref])
    assert verified == {ref.uri: content_file_uri(content.inventory_uri, name)}


async def test_a_missing_or_resized_content_file_is_refused(world):
    (content, _), _ = await world.nuscenes()
    ref = _file_ref(content, "LICENSE", b"license a\n")
    uri = content_file_uri(content.inventory_uri, "LICENSE")
    await world.store.write_bytes(uri, b"license")
    with pytest.raises(PayloadRegistrationError, match="has 7 bytes"):
        await verify_content_payload_refs(world.context, content, [ref])
    await world.store.delete(uri)
    with pytest.raises(PayloadRegistrationError, match="has no object at"):
        await verify_content_payload_refs(world.context, content, [ref])


async def test_a_content_stored_outside_the_sources_root_is_refused(world):
    (content, _), _ = await world.nuscenes(sources_root=f"{world.root}/other-sources")
    ref = _file_ref(content, "LICENSE", b"license a\n")
    with pytest.raises(PayloadRegistrationError, match="not stored under the sources"):
        await verify_content_payload_refs(world.context, content, [ref])


async def test_a_lerobot_frame_reference_of_a_missing_video_is_refused(world):
    content, episodes = await world.lerobot_episodes()
    manifest = episodes[0]
    await world.store.delete(
        content_file_uri(content.inventory_uri, lerobot_synthetic.VIDEO_PATH)
    )
    with pytest.raises(PayloadRegistrationError, match="has no object at"):
        await verify_content_payload_refs(
            world.context, content, _episode_refs(manifest)
        )
