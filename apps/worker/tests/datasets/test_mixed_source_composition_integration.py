"""Mixed-source DatasetVersions on real PostgreSQL and real MinIO (ADR-015 §3,
§4, §7; MS-1, MS-6, MS-8, MS-14; M3 S5).

One DatasetVersion pins revisions of every (domain, source kind): a Scene and
an Episode of a RobotRun, Scenes of a preserved nuScenes content and Episodes
of a preserved LeRobot content. Composition by pins and by selector converges
on one composition; conflicts, invalid source bindings, wrong and duplicate
pins and revisions that never passed the registration gate write nothing;
composing changes no unit, revision or manifest; no content operation creates
a RobotRun.

The contents go through the production path (publish_snapshot,
REGISTER_SOURCE_SNAPSHOT, BUILD_SNAPSHOT_*, REGISTER_*), each test with its own
synthetic trees. The RobotRun's revisions are seeded through the repositories,
as in test_dataset_composition_integration.py (their registration gate is
covered by the recording registration tests). Requires the disposable database
and bucket of `make test-integration`; skips (and so fails there) otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from sceneops_core.datasets.composition import composition_checksum
from sceneops_core.jobs.schemas import (
    BuildSnapshotEpisodesJobParams,
    BuildSnapshotScenesJobParams,
    ComposeDatasetVersionJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.provenance import RevisionPin, manifest_artifact_id_for
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_db.models.datasets import DatasetVersionModel
from sceneops_db.session import get_async_sessionmaker
from sceneops_sources import content_file_uri, publish_snapshot
from sceneops_sources import lerobot_synthetic, nuscenes_synthetic
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.datasets.composition import (
    CompositionInputError,
    DatasetVersionConflictError,
    compose_dataset_version,
)
from sceneops_worker.episodes.registration import register_episodes
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_snapshot_episodes import (
    BuildSnapshotEpisodesJobHandler,
)
from sceneops_worker.jobs.dataset.build_snapshot_scenes import (
    BuildSnapshotScenesJobHandler,
)
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    register_scenes,
)
from sceneops_worker.sources.registration import register_source_snapshot
from tests.datasets.test_dataset_composition_integration import _World

SCENE_CONFIG = {"channels": sorted(nuscenes_synthetic.CHANNELS)}
EPISODE_CONFIG = {
    "features": [
        {"feature": "observation.image", "role": "observation"},
        {"feature": "observation.state", "role": "state"},
        {"feature": "action", "role": "action"},
        {"feature": "next.done", "role": "transition"},
    ]
}
_BUILDS = {
    "nuscenes": (BuildSnapshotScenesJobHandler, BuildSnapshotScenesJobParams, JobType.BUILD_SNAPSHOT_SCENES),
    "lerobot": (BuildSnapshotEpisodesJobHandler, BuildSnapshotEpisodesJobParams, JobType.BUILD_SNAPSHOT_EPISODES),
}  # fmt: skip


class _Mixed:
    """A RobotRun with two Scenes and one Episode under one fingerprint each
    (``world``; its other Scene revisions stay out), a nuScenes content with
    two Scenes and a LeRobot content with three Episodes, all registered, and
    a Dataset to compose them into."""

    def __init__(self, world: _World, settings, run_id: str, tmp_path: Path) -> None:
        self.world = world
        self.settings = settings
        self.run_id = run_id
        self.store = S3ArtifactStore(settings=settings.artifact)
        self.nuscenes_root = nuscenes_synthetic.write_nuscenes_tree(
            tmp_path / "nuscenes", scenes=2, samples=3, sweeps=1
        )
        (self.nuscenes_root / "LICENSE").write_text(f"{run_id}\n")
        self.lerobot_root = lerobot_synthetic.write_lerobot_tree(
            tmp_path / "lerobot", lengths=(3, 2, 4)
        )
        (self.lerobot_root / "README.md").write_text(f"# {run_id}\n")
        self.jobs = 0
        self.content: dict[str, str] = {}
        self.snapshot: dict[str, str] = {}
        self.builds: dict[str, object] = {}
        self.pins: dict[str, list[RevisionPin]] = {}

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    async def publish(self, fmt: str, revision: str = "a") -> tuple[str, str]:
        root, include = (
            (self.nuscenes_root, nuscenes_synthetic.INCLUDE)
            if fmt == "nuscenes"
            else (self.lerobot_root, lerobot_synthetic.INCLUDE)
        )
        published = await publish_snapshot(
            store=self.store,
            sources_root=self.settings.artifact.source_root_uri,
            source_dir=root,
            declaration=SnapshotDeclaration(
                format=fmt,
                upstream=f"test/{self.run_id}-{fmt}",
                upstream_revision=revision,
                selection=SnapshotSelection(include=include),
            ),
            annotations={"license": "test"},
        )
        async with get_async_sessionmaker()() as session:
            registered = await register_source_snapshot(
                context=self.context(session), descriptor_uri=published.descriptor_uri
            )
        return registered.snapshot.snapshot_id, registered.content.content_id

    async def build(self, fmt: str, snapshot_id: str, config: dict):
        handler, params_model, job_type = _BUILDS[fmt]
        self.jobs += 1
        params = params_model.model_validate(
            {"snapshot_id": snapshot_id, "build_config": config}
        )
        async with get_async_sessionmaker()() as session:
            return await handler().run(
                JobHandlerRequest(
                    job=JobManifest(
                        job_id=f"job-{self.run_id}-{self.jobs}", type=job_type,
                        status=JobStatus.RUNNING, params=params.model_dump(mode="json"),
                    ),
                    params=params,
                    context=self.context(session),
                )
            )  # fmt: skip

    async def register(self, fmt: str, artifact_ids):
        registrar = register_scenes if fmt == "nuscenes" else register_episodes
        async with get_async_sessionmaker()() as session:
            return await registrar(
                context=self.context(session), manifest_artifact_ids=artifact_ids
            )

    async def setup(self) -> None:
        await self.world.setup()
        for fmt, config in (("nuscenes", SCENE_CONFIG), ("lerobot", EPISODE_CONFIG)):
            self.snapshot[fmt], self.content[fmt] = await self.publish(fmt)
            build = await self.build(fmt, self.snapshot[fmt], config)
            self.builds[fmt] = build
            registration = await self.register(fmt, build.manifest_artifact_ids)
            self.pins[fmt] = [r.pin for r in registration.revisions]
        self.pins["recording"] = self.world.pins(("a", "all"), ("b", "all"), "e")

    def all_pins(self) -> list[RevisionPin]:
        return self.pins["recording"] + self.pins["nuscenes"] + self.pins["lerobot"]

    def selector(self) -> dict:
        recording_scene, recording_episode = (
            self.world.scenes[("a", "all")], self.world.episodes["e"]
        )  # fmt: skip
        clauses = [
            ("scene", "recording", self.world.run_id, recording_scene.producer_fingerprint),
            ("episode", "recording", self.world.run_id, recording_episode.producer_fingerprint),
            ("scene", "content", self.content["nuscenes"], self.builds["nuscenes"].producer_fingerprint),
            ("episode", "content", self.content["lerobot"], self.builds["lerobot"].producer_fingerprint),
        ]  # fmt: skip
        keys = ("domain", "source_kind", "source_id", "producer_fingerprint")
        return {"sources": [dict(zip(keys, c)) for c in clauses]}

    async def compose(self, version: str, **members):
        async with get_async_sessionmaker()() as session:
            return await compose_dataset_version(
                context=self.context(session),
                params=ComposeDatasetVersionJobParams.model_validate(
                    {
                        "dataset_id": self.world.dataset_id,
                        "dataset_version": version,
                        **members,
                    }
                ),
            )

    async def version_count(self) -> int:
        return await self.world.version_count()

    async def state(self) -> dict[str, object]:
        """Every row a composition must leave unchanged: the units and
        revisions of all three sources, the artifacts they own, the RobotRuns."""
        content_ids = tuple(self.content.values())
        queries = {
            "scenes": "SELECT scene_id, source_kind, robot_run_id, content_id, unit_key FROM scenes "
            "WHERE content_id = ANY(:c) OR robot_run_id = :r ORDER BY scene_id",
            "scene_revisions": "SELECT r.scene_id, r.manifest_checksum, r.producer_fingerprint FROM scene_revisions r "
            "JOIN scenes s USING (scene_id) WHERE s.content_id = ANY(:c) OR s.robot_run_id = :r ORDER BY 1, 2",
            "episodes": "SELECT episode_id, source_kind, robot_run_id, content_id, unit_key FROM episodes "
            "WHERE content_id = ANY(:c) OR robot_run_id = :r ORDER BY episode_id",
            "episode_revisions": "SELECT r.episode_id, r.manifest_checksum, r.producer_fingerprint FROM episode_revisions r "
            "JOIN episodes e USING (episode_id) WHERE e.content_id = ANY(:c) OR e.robot_run_id = :r ORDER BY 1, 2",
            "artifacts": "SELECT artifact_id, checksum, job_id FROM artifacts WHERE owner_id IN "
            "(SELECT scene_id FROM scenes WHERE content_id = ANY(:c) OR robot_run_id = :r "
            "UNION SELECT episode_id FROM episodes WHERE content_id = ANY(:c) OR robot_run_id = :r) ORDER BY 1",
            "robot_runs": "SELECT count(*) FROM robot_runs",
        }  # fmt: skip
        async with get_async_sessionmaker()() as session:
            rows = {
                name: (await session.execute(text(sql), {"c": list(content_ids), "r": self.world.run_id})).all()
                for name, sql in queries.items()
            }  # fmt: skip
        rows["objects"] = sorted(
            (o.uri, o.size_bytes) for o in await self.store.list_objects(self.settings.artifact_root_uri)
        )  # fmt: skip
        return rows


@pytest.fixture()
async def mixed(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    async with get_async_sessionmaker()() as session:
        robot_runs = (
            await session.execute(text("SELECT count(*) FROM robot_runs"))
        ).scalar_one()
    env = _Mixed(_World(unique_id), worker_settings, unique_id("mixed"), tmp_path)
    await env.setup()
    # One RobotRun: the seeded one. Publishing, registering and building
    # external content never creates a RobotRun (ADR-015 §3, MS-1).
    async with get_async_sessionmaker()() as session:
        after = (
            await session.execute(text("SELECT count(*) FROM robot_runs"))
        ).scalar_one()
    assert after == robot_runs + 1
    yield env
    dependencies_module._artifact_store = None


# --- composition ------------------------------------------------------------------------


async def test_one_version_pins_every_source_and_domain_by_pins_and_by_selector(mixed):
    before = await mixed.state()
    pins = mixed.all_pins()

    by_pins = await mixed.compose(
        "pins", pins=[p.model_dump(mode="json") for p in pins]
    )

    assert (by_pins.created, by_pins.member_count) == (True, 2 + 1 + 2 + 3)
    version = by_pins.version
    assert version.composition_checksum == composition_checksum(pins)
    nuscenes = mixed.builds["nuscenes"]
    recording = [mixed.world.scenes[(k, "all")] for k in ("a", "b")]
    assert version.scene.scene_count == 4
    assert (
        version.scene.observation_count
        == sum(r.observation_count for r in recording) + nuscenes.observation_count
    )
    assert (
        version.scene.keyframe_count
        == sum(r.keyframe_count for r in recording) + nuscenes.keyframe_count
    )
    assert version.scene.observed_channels == sorted(
        {c for r in recording for c in r.observed_channels}
        | set(SCENE_CONFIG["channels"])
    )
    assert version.episode.episode_count == 1 + 3
    _, members = await mixed.world.rows("pins")
    assert members == sorted((p.unit_id, p.manifest_checksum) for p in pins)

    # The same composition, chosen by one clause per (domain, source).
    by_selector = await mixed.compose("selector", selector=mixed.selector())
    assert by_selector.version.composition_checksum == version.composition_checksum
    assert [(c.domain, c.source_kind) for c in by_selector.version.selector.sources] == [
        ("episode", "content"), ("episode", "recording"), ("scene", "content"), ("scene", "recording"),
    ]  # fmt: skip

    # Retrying either converges and writes nothing.
    again = await mixed.compose(
        "pins", pins=[p.model_dump(mode="json") for p in reversed(pins)]
    )
    assert (again.created, again.version) == (False, version)
    assert (await mixed.compose("selector", selector=mixed.selector())).created is False
    assert await mixed.version_count() == 2

    # Composing changed no unit, revision, manifest record, object or RobotRun.
    assert await mixed.state() == before


async def test_unit_identity_never_depends_on_membership(mixed):
    """Every unit id is f(domain, source kind, source id, unit key); the same
    revisions in two versions are one unit each, and no manifest names a
    DatasetVersion."""
    await mixed.compose(
        "one", pins=[p.model_dump(mode="json") for p in mixed.all_pins()]
    )
    await mixed.compose(
        "two", pins=[p.model_dump(mode="json") for p in mixed.pins["lerobot"]]
    )
    state = await mixed.state()
    assert len(state["scenes"]) == 2 + 2 and len(state["episodes"]) == 1 + 3
    for domain in ("scene", "episode"):
        for unit_id, kind, robot_run_id, content_id, unit_key in state[f"{domain}s"]:
            # A content unit names no RobotRun; a recording unit no content.
            assert (robot_run_id is None, content_id is None) == (
                kind == "content",
                kind == "recording",
            )
            assert unit_id == _unit_id(
                domain, kind, robot_run_id or content_id, unit_key
            )
    # The content manifests as stored (the seeded RobotRun revisions have
    # records only).
    for pin in mixed.pins["nuscenes"] + mixed.pins["lerobot"]:
        async with get_async_sessionmaker()() as session:
            record = await mixed.context(session).artifact_record_store.get(
                pin.manifest_artifact_id
            )
        data = await mixed.store.read_bytes(record.uri)
        for name in (mixed.world.dataset_id, "one", "two"):
            assert json.dumps(name).encode() not in data


def _unit_id(domain: str, source_kind: str, source_id: str, unit_key: str) -> str:
    """ingress-boundaries.md §6, written out here: no DatasetVersion, snapshot
    or build input enters it."""
    document = {"unit_id_schema": "sceneops.unit_id/v2", "domain": domain,
                "source_kind": source_kind, "source_id": source_id, "unit_key": unit_key}  # fmt: skip
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return f"{domain}-{hashlib.sha256(canonical.encode()).hexdigest()[:32]}"


# --- refusals: nothing is written ----------------------------------------------------------


async def test_a_name_bound_to_a_mixed_composition_is_never_rebound(mixed):
    await mixed.compose("v", pins=[p.model_dump(mode="json") for p in mixed.all_pins()])
    without_lerobot = mixed.pins["recording"] + mixed.pins["nuscenes"]
    with pytest.raises(DatasetVersionConflictError):
        await mixed.compose(
            "v", pins=[p.model_dump(mode="json") for p in without_lerobot]
        )
    with pytest.raises(DatasetVersionConflictError):
        selector = mixed.selector()
        selector["sources"] = selector["sources"][:3]
        await mixed.compose("v", selector=selector)
    _, members = await mixed.world.rows("v")
    assert len(members) == 8


@pytest.mark.parametrize(
    "clause",
    [
        # A content clause naming the RobotRun, a recording clause naming a content.
        lambda m: ("scene", "content", m.world.run_id, m.world.scenes[("a", "all")].producer_fingerprint),
        lambda m: ("episode", "recording", m.content["lerobot"], m.builds["lerobot"].producer_fingerprint),
        # The nuScenes content has no Episode; the LeRobot content no Scene.
        lambda m: ("episode", "content", m.content["nuscenes"], m.builds["nuscenes"].producer_fingerprint),
        lambda m: ("scene", "content", m.content["lerobot"], m.builds["lerobot"].producer_fingerprint),
        # The right source under another source's producer fingerprint.
        lambda m: ("scene", "content", m.content["nuscenes"], m.builds["lerobot"].producer_fingerprint),
    ],
    ids=["content-names-robot-run", "recording-names-content", "nuscenes-episodes", "lerobot-scenes", "foreign-fingerprint"],
)  # fmt: skip
async def test_a_selector_clause_bound_to_the_wrong_source_selects_nothing(
    mixed, clause
):
    keys = ("domain", "source_kind", "source_id", "producer_fingerprint")
    selector = mixed.selector()
    selector["sources"].append(dict(zip(keys, clause(mixed))))
    with pytest.raises(CompositionInputError, match="selects no registered"):
        await mixed.compose("bad", selector=selector)
    assert await mixed.version_count() == 0


async def test_a_pin_of_another_units_revision_names_nothing(mixed):
    """A content Scene's unit with a LeRobot Episode manifest's checksum, its
    artifact id recomputed: a self-consistent pin of no registered revision."""
    scene, episode = mixed.pins["nuscenes"][0], mixed.pins["lerobot"][0]
    wrong = RevisionPin(
        domain="scene",
        unit_id=scene.unit_id,
        manifest_checksum=episode.manifest_checksum,
        manifest_artifact_id=manifest_artifact_id_for(unit_id=scene.unit_id, manifest_checksum=episode.manifest_checksum),
    )  # fmt: skip
    pins = [p for p in mixed.all_pins() if p != scene] + [wrong]
    with pytest.raises(CompositionInputError, match="name no registered revision"):
        await mixed.compose("bad", pins=[p.model_dump(mode="json") for p in pins])
    # A pin whose artifact id is another revision's is not even a pin.
    with pytest.raises(ValidationError):
        RevisionPin(
            domain="scene", unit_id=scene.unit_id, manifest_checksum=scene.manifest_checksum,
            manifest_artifact_id=episode.manifest_artifact_id,
        )  # fmt: skip
    assert await mixed.version_count() == 0


async def test_two_revisions_of_one_content_unit_are_refused(mixed):
    """Another channel selection is a second revision of each nuScenes Scene;
    a version pins at most one, whether by pins or by two selector clauses."""
    narrower = {"channels": ["CAM_FRONT", "LIDAR_TOP"]}
    build = await mixed.build("nuscenes", mixed.snapshot["nuscenes"], narrower)
    second = [
        r.pin
        for r in (
            await mixed.register("nuscenes", build.manifest_artifact_ids)
        ).revisions
    ]
    assert {p.unit_id for p in second} == {p.unit_id for p in mixed.pins["nuscenes"]}
    with pytest.raises(ValidationError, match="at most one revision per unit"):
        ComposeDatasetVersionJobParams.model_validate(
            {"dataset_id": mixed.world.dataset_id, "dataset_version": "bad",
             "pins": [p.model_dump(mode="json") for p in mixed.all_pins() + second[:1]]}
        )  # fmt: skip
    selector = mixed.selector()
    selector["sources"].append(
        {"domain": "scene", "source_kind": "content", "source_id": mixed.content["nuscenes"],
         "producer_fingerprint": build.producer_fingerprint}
    )  # fmt: skip
    with pytest.raises(CompositionInputError, match="at most one revision per unit"):
        await mixed.compose("bad", selector=selector)
    assert await mixed.version_count() == 0


async def test_a_revision_whose_referenced_file_is_missing_never_becomes_composable(
    mixed,
):
    """The registration gate refuses a manifest whose preserved file is not in
    the store, so no revision row exists and a pin of it composes nothing."""
    narrower = {"channels": ["LIDAR_TOP"]}
    build = await mixed.build("nuscenes", mixed.snapshot["nuscenes"], narrower)
    async with get_async_sessionmaker()() as session:
        context = mixed.context(session)
        record = await context.artifact_record_store.get(build.manifest_artifact_ids[0])
        manifest = await context.scene_artifact_store.read_pinned_manifest(
            uri=record.uri, checksum=record.checksum, size_bytes=record.size_bytes
        )
        content = await context.source_store.get_content(mixed.content["nuscenes"])
        inventory = await context.artifact_record_store.get(
            content.inventory_artifact_id
        )
    missing = manifest.observations[0].payload
    path = missing.logical_uri().key.removeprefix(
        f"contents/{content.content_id}/files/"
    )
    await mixed.store.delete_prefix(content_file_uri(inventory.uri, path))
    with pytest.raises(SceneManifestRejectedError, match="has no object at"):
        await mixed.register("nuscenes", build.manifest_artifact_ids)
    pin = RevisionPin(
        domain="scene", unit_id=scene_id_for(manifest.lineage.source),
        manifest_checksum=record.checksum, manifest_artifact_id=record.artifact_id,
    )  # fmt: skip
    with pytest.raises(CompositionInputError, match="name no registered revision"):
        await mixed.compose(
            "bad",
            pins=[p.model_dump(mode="json") for p in mixed.pins["recording"] + [pin]],
        )
    assert await mixed.version_count() == 0


# --- concurrency -----------------------------------------------------------------------


async def test_concurrent_mixed_compositions_of_one_name_converge_or_conflict(mixed):
    pins = [p.model_dump(mode="json") for p in mixed.all_pins()]
    identical = await asyncio.gather(
        *(mixed.compose("same", pins=pins) for _ in range(4))
    )
    assert sorted(c.created for c in identical) == [False, False, False, True]
    assert len({c.version.composition_checksum for c in identical}) == 1

    others = [p.model_dump(mode="json") for p in mixed.pins["lerobot"]]
    outcomes = await asyncio.gather(
        mixed.compose("contested", pins=pins),
        mixed.compose(
            "contested",
            selector=mixed.selector() | {"sources": mixed.selector()["sources"][:1]},
        ),
        mixed.compose("contested", pins=others),
        return_exceptions=True,
    )
    committed = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(committed) == 1 and committed[0].created is True
    assert all(
        isinstance(o, DatasetVersionConflictError)
        for o in outcomes
        if o not in committed
    )
    async with get_async_sessionmaker()() as session:
        row = (
            await session.execute(
                select(DatasetVersionModel.composition_checksum).where(
                    DatasetVersionModel.id == f"{mixed.world.dataset_id}:contested"
                )
            )
        ).scalar_one()
        count = (
            await session.execute(
                select(func.count())
                .select_from(DatasetVersionModel)
                .where(DatasetVersionModel.dataset_id == mixed.world.dataset_id)
            )
        ).scalar_one()
    assert row == committed[0].version.composition_checksum
    assert count == 2
