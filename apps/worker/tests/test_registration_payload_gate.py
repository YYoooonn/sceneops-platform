"""REGISTER_SCENES / REGISTER_EPISODES verify every payload reference of the
input against the unit's own registered source before any revision is
registered (ADR-017): a manifest whose references the gate cannot back is
rejected with no revision written, and one it backs is registered. Bytes of
an earlier manifest version are refused by the loader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.config import ArtifactSettings
from sceneops_core.episodes import testing as episode_testing
from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig
from sceneops_core.episodes.schemas import episode_id_for
from sceneops_core.provenance import (
    PinnedContent,
    RevisionDecision,
    manifest_artifact_id_for,
)
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_core.scenes import testing as scene_testing
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.sources import (
    SnapshotDeclaration,
    SnapshotSelection,
    SourceContentRecord,
)
from sceneops_episodes.artifacts import EpisodeArtifactStore
from sceneops_episodes.lerobot_builder import plan_lerobot_episodes
from sceneops_recording.observations.store import ObservationPayloadStore
from sceneops_scenes.artifacts import SceneArtifactStore
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes
from sceneops_sources import (
    LocalContentFiles,
    lerobot_synthetic,
    nuscenes_synthetic,
    publish_snapshot,
)
from sceneops_storage import LocalArtifactStore
from sceneops_worker.episodes.registration import (
    EpisodeManifestRejectedError,
    register_episodes,
)
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    register_scenes,
)

RUN = "run-001"
RECORDING_CHECKSUM = "sha256:" + "1" * 64


class _Records:
    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    async def get(self, artifact_id):
        return self.records.get(artifact_id)

    async def get_many(self, ids):
        return {i: self.records[i] for i in ids if i in self.records}


class _Revisions:
    """Registers every revision it is given as new."""

    def __init__(self) -> None:
        self.registered: list = []

    async def register_revision(self, revision):
        self.registered.append(revision)
        return SimpleNamespace(decision=RevisionDecision.NEW_UNIT, revision=revision)


class _World:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        root = str(tmp_path / "artifacts")
        self.settings = ArtifactSettings(root_uri=root)
        self.store = LocalArtifactStore(root_uri=root)
        self.records = _Records()
        self.contents: dict[str, SourceContentRecord] = {}
        self.scenes, self.episodes = _Revisions(), _Revisions()
        self.payloads = ObservationPayloadStore(
            artifact_store=self.store,
            payload_root_uri=self.settings.observation_payload_root_uri,
        )

        async def get_content(content_id):
            return self.contents.get(content_id)

        async def get_run(run_id):
            return _run() if run_id == RUN else None

        async def done() -> None:
            return None

        self.records.records[f"art-recording-{RUN}"] = ArtifactRecord(
            artifact_id=f"art-recording-{RUN}",
            kind=ArtifactKind.ROBOT_RUN_RECORDING,
            uri=f"{root}/recordings/{RUN}.mcap",
            checksum=RECORDING_CHECKSUM,
        )
        self.context = SimpleNamespace(
            settings=SimpleNamespace(artifact=self.settings),
            artifact_store=self.store,
            artifact_record_store=self.records,
            source_store=SimpleNamespace(get_content=get_content),
            robot_store=SimpleNamespace(get_run=get_run),
            scene_store=self.scenes,
            episode_store=self.episodes,
            scene_artifact_store=SceneArtifactStore(
                artifact_store=self.store,
                canonical_root_uri=f"{root}/canonical",
                payload_root_uri=self.settings.observation_payload_root_uri,
            ),
            episode_artifact_store=EpisodeArtifactStore(
                artifact_store=self.store,
                canonical_root_uri=f"{root}/canonical",
                dataset_root_uri=f"{root}/datasets",
                payload_root_uri=self.settings.observation_payload_root_uri,
            ),
            commit=done,
            rollback=done,
        )

    async def publish_payloads(self, refs, data_of) -> None:
        """Publish and register each referenced payload, as a recording build
        does."""
        for ref in refs:
            artifact_id = ref.logical_uri().key.rpartition("/")[2]
            data = data_of(ref)
            uri, _ = await self.payloads.publish(
                robot_run_id=RUN,
                artifact_id=artifact_id,
                data=data,
                checksum=sha256_checksum(data),
            )
            self.records.records[artifact_id] = ArtifactRecord(
                artifact_id=artifact_id,
                kind=ArtifactKind.OBSERVATION_PAYLOAD,
                uri=uri,
                media_type=ref.media_type,
                size_bytes=len(data),
                checksum=sha256_checksum(data),
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=RUN,
            )

    async def content(self, tree, fmt: str, include):
        """Publish ``tree`` and register its content; returns its inventory."""
        published = await publish_snapshot(
            store=self.store,
            sources_root=self.settings.source_root_uri,
            source_dir=tree,
            declaration=SnapshotDeclaration(
                format=fmt,
                upstream="test/registration-gate",
                upstream_revision=tree.name,
                selection=SnapshotSelection(include=tuple(include)),
            ),
        )
        inventory = published.inventory
        inventory_id = f"art-inventory-{inventory.content_id}"
        self.records.records[inventory_id] = ArtifactRecord(
            artifact_id=inventory_id,
            kind=ArtifactKind.SOURCE_CONTENT_INVENTORY,
            uri=published.inventory_uri,
        )
        self.contents[inventory.content_id] = SourceContentRecord(
            content_id=inventory.content_id,
            format=fmt,
            content_checksum=inventory.content_checksum,
            inventory_artifact_id=inventory_id,
            file_count=len(inventory.files),
            total_bytes=sum(f.size_bytes for f in inventory.files),
        )
        return inventory

    async def publish_scene(self, manifest, *, data: bytes | None = None) -> str:
        scene_id = scene_id_for(manifest.lineage.source)
        published = await self.context.scene_artifact_store.publish_canonical_manifest(
            scene_id=scene_id, manifest=manifest
        )
        if data is not None:
            await self.store.write_bytes(published.uri, data)
        return self._manifest_record(
            ArtifactKind.SCENE_MANIFEST,
            scene_id,
            published.uri,
            data if data is not None else manifest.to_canonical_bytes(),
        )

    async def publish_episode(self, manifest) -> str:
        episode_id = episode_id_for(manifest.lineage.source)
        published = (
            await self.context.episode_artifact_store.publish_canonical_manifest(
                episode_id=episode_id, manifest=manifest
            )
        )
        return self._manifest_record(
            ArtifactKind.EPISODE_MANIFEST,
            episode_id,
            published.uri,
            manifest.to_canonical_bytes(),
        )

    def _manifest_record(self, kind, unit_id: str, uri: str, data: bytes) -> str:
        checksum = sha256_checksum(data)
        artifact_id = manifest_artifact_id_for(
            unit_id=unit_id, manifest_checksum=checksum
        )
        self.records.records[artifact_id] = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            uri=uri,
            size_bytes=len(data),
            checksum=checksum,
        )
        return artifact_id


def _run() -> RobotRunRecord:
    return RobotRunRecord(
        run_id=RUN,
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=f"art-recording-{RUN}",
        manifest_artifact_id=f"art-robotrunmanifest-{RUN}",
        manifest_checksum="sha256:" + "2" * 64,
    )


@pytest.fixture()
def world(tmp_path) -> _World:
    return _World(tmp_path)


def _recording_scene():
    return scene_testing.build_scene_manifest(
        source=scene_testing.recording_source(
            robot_run_id=RUN, recording_checksum=RECORDING_CHECKSUM
        )
    )


def _scene_bytes(ref, manifest) -> bytes:
    (observation,) = [o for o in manifest.observations if o.payload == ref]
    return scene_testing.payload_bytes(observation.observation_id)


# ── recording units ────────────────────────────────────────────────────────


async def test_a_scene_whose_payloads_are_not_registered_is_rejected(world):
    manifest = _recording_scene()
    artifact_id = await world.publish_scene(manifest)
    with pytest.raises(SceneManifestRejectedError, match="is not registered"):
        await register_scenes(
            context=world.context, manifest_artifact_ids=[artifact_id]
        )
    assert world.scenes.registered == []


async def test_a_scene_whose_payloads_are_registered_is_registered(world):
    manifest = _recording_scene()
    await world.publish_payloads(
        [o.payload for o in manifest.observations],
        lambda ref: _scene_bytes(ref, manifest),
    )
    artifact_id = await world.publish_scene(manifest)
    registration = await register_scenes(
        context=world.context, manifest_artifact_ids=[artifact_id]
    )
    assert registration.scene_ids() == [scene_id_for(manifest.lineage.source)]
    assert len(world.scenes.registered) == 1


async def test_a_scene_referencing_a_lost_payload_object_is_rejected(world):
    manifest = _recording_scene()
    refs = [o.payload for o in manifest.observations]
    await world.publish_payloads(refs, lambda ref: _scene_bytes(ref, manifest))
    lost = world.records.records[refs[0].logical_uri().key.rpartition("/")[2]]
    world.store._to_path(lost.uri).unlink()
    artifact_id = await world.publish_scene(manifest)
    with pytest.raises(SceneManifestRejectedError, match="has no object at"):
        await register_scenes(
            context=world.context, manifest_artifact_ids=[artifact_id]
        )
    assert world.scenes.registered == []


async def test_an_episode_whose_payloads_are_not_registered_is_rejected(world):
    manifest = episode_testing.episode_manifest(
        [
            episode_testing.observation("/cam", 0),
            episode_testing.state("/odom", 1_000_000_000, x=1.0),
        ],
        robot_run_id=RUN,
        recording_checksum=RECORDING_CHECKSUM,
        window=(0, 2_000_000_000),
    )
    artifact_id = await world.publish_episode(manifest)
    with pytest.raises(EpisodeManifestRejectedError, match="is not registered"):
        await register_episodes(
            context=world.context, manifest_artifact_ids=[artifact_id]
        )
    assert world.episodes.registered == []

    await world.publish_payloads(
        [o.payload for o in manifest.occurrences() if o.payload is not None],
        lambda ref: episode_testing.payload_bytes("/cam", 0),
    )
    registration = await register_episodes(
        context=world.context, manifest_artifact_ids=[artifact_id]
    )
    assert len(registration.revisions) == len(world.episodes.registered) == 1


async def test_earlier_manifest_versions_are_refused(world):
    manifest = _recording_scene()
    document = json.loads(manifest.to_canonical_bytes())
    document["schema_version"] = "sceneops.scene_manifest/v4"
    data = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    artifact_id = await world.publish_scene(manifest, data=data)
    with pytest.raises(SceneManifestRejectedError, match="unsupported"):
        await register_scenes(
            context=world.context, manifest_artifact_ids=[artifact_id]
        )


# ── content units ──────────────────────────────────────────────────────────


async def _nuscenes_scenes(world, name: str):
    tree = nuscenes_synthetic.write_nuscenes_tree(
        world.tmp_path / f"nuscenes-{name}", scenes=1, samples=2, sweeps=1
    )
    (tree / "LICENSE").write_text(f"license {name}\n")
    inventory = await world.content(tree, "nuscenes", nuscenes_synthetic.INCLUDE)
    return plan_nuscenes_scenes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(tree, inventory),
        config=NuScenesSceneBuildConfig(channels=sorted(nuscenes_synthetic.CHANNELS)),
    ).scenes


async def test_a_native_scene_registers_against_its_own_content_only(world):
    (own,) = await _nuscenes_scenes(world, "a")
    (other,) = await _nuscenes_scenes(world, "b")
    artifact_id = await world.publish_scene(own.manifest)
    registration = await register_scenes(
        context=world.context, manifest_artifact_ids=[artifact_id]
    )
    assert registration.source_kind == "content"
    assert len(world.scenes.registered) == 1

    # The same Scene pointing at the other content's files (identical bytes
    # there) is not this content's Scene.
    document = json.loads(own.manifest.to_canonical_bytes())
    own_id = own.manifest.lineage.source.content_id
    other_id = other.manifest.lineage.source.content_id
    for observation in document["observations"]:
        observation["payload"]["uri"] = observation["payload"]["uri"].replace(
            own_id, other_id
        )
    swapped = type(own.manifest).model_validate(document)
    artifact_id = await world.publish_scene(swapped)
    with pytest.raises(SceneManifestRejectedError, match="does not name a file"):
        await register_scenes(
            context=world.context, manifest_artifact_ids=[artifact_id]
        )
    assert len(world.scenes.registered) == 1


async def test_a_lerobot_episode_registers_with_its_frame_selectors(world):
    tree = lerobot_synthetic.write_lerobot_tree(world.tmp_path / "lerobot")
    inventory = await world.content(tree, "lerobot", lerobot_synthetic.INCLUDE)
    plan = plan_lerobot_episodes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(tree, inventory),
        config=LeRobotEpisodeBuildConfig(
            features=[
                {"feature": "observation.image", "role": "observation"},
                {"feature": "observation.state", "role": "state"},
            ]
        ),
    )
    ids = [await world.publish_episode(p.manifest) for p in plan.episodes]
    registration = await register_episodes(
        context=world.context, manifest_artifact_ids=ids
    )
    assert len(registration.revisions) == len(plan.episodes)
    assert all(
        o.payload.selector is not None
        for p in plan.episodes
        for o in p.manifest.observations
    )
