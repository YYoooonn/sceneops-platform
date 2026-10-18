"""COMPOSE_DATASET_VERSION against real PostgreSQL (ADR-015 §7, MS-8).

A DatasetVersion is committed with all of its members, summaries and
selector in one transaction, or not at all; a name converges on one
composition and fails on another; concurrent compositions are decided by
the name's unique key; a Job re-run after its composition committed
converges; composition makes no object-store call.

Revisions are seeded through the repositories (registration and its gate
are covered by the registration integration tests). Requires
SCENEOPS_DATABASE_URL (a database migrated to head); skips otherwise. Runs
in the disposable database of `make test-integration`; every test uses its
own ids.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.datasets.composition import composition_checksum
from sceneops_core.datasets.schemas import DatasetRecord
from sceneops_core.episodes.schemas import episode_id_for, project_episode_revision
from sceneops_core.episodes.testing import episode_manifest, state
from sceneops_core.jobs.schemas import (
    ComposeDatasetVersionJobParams,
    JobManifest,
    JobStatus,
    JobType,
    parse_job_result,
)
from sceneops_core.provenance import RevisionPin, manifest_artifact_id_for
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_core.scenes.schemas import project_scene_revision, scene_id_for
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_db.models.datasets import DatasetVersionMemberModel, DatasetVersionModel
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.datasets import PostgresDatasetRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.postgres.robots import (
    PostgresRobotRepository,
    PostgresRobotRunRepository,
)
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_execution.jobs.lease_recovery import (
    LeaseRecoveryOutcome,
    recover_expired_leases,
)
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.datasets.composition import (
    CompositionInputError,
    DatasetVersionConflictError,
    compose_dataset_version,
)
from sceneops_worker.jobs.runner import JobRunner

RECORDING_CHECKSUM = "sha256:" + "1" * 64

pytestmark = pytest.mark.usefixtures("_fresh_database_connection")


# --- seeding ------------------------------------------------------------------------


async def _seed_run(session, run_id: str) -> None:
    robot_id = f"robot-{run_id}"
    await PostgresRobotRepository(session).create(RobotRecord(robot_id=robot_id))
    artifacts = PostgresArtifactRefRepository(session)
    for artifact_id, kind, checksum in (
        (
            robot_run_recording_artifact_id(run_id),
            ArtifactKind.ROBOT_RUN_RECORDING,
            RECORDING_CHECKSUM,
        ),
        (
            robot_run_manifest_artifact_id(run_id),
            ArtifactKind.ROBOT_RUN_MANIFEST,
            "sha256:" + "0" * 64,
        ),
    ):
        await artifacts.create(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=kind,
                uri=f"s3://sceneops-test/robot_runs/{run_id}/{artifact_id}",
                size_bytes=1,
                checksum=checksum,
            ),
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=run_id,
        )
    await PostgresRobotRunRepository(session).create(
        RobotRunRecord(
            run_id=run_id,
            robot_id=robot_id,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            recording_format="mcap",
            source_clock="mcap_log_time",
            recording_artifact_id=robot_run_recording_artifact_id(run_id),
            manifest_artifact_id=robot_run_manifest_artifact_id(run_id),
            manifest_checksum="sha256:" + "0" * 64,
        )
    )


async def _manifest_artifact(session, *, artifact_id, kind, owner_type, owner_id, data):
    await PostgresArtifactRefRepository(session).create(
        artifact_id=artifact_id,
        ref=ArtifactRef(
            kind=kind,
            uri=f"s3://sceneops-test/canonical/{owner_id}/{artifact_id}.json",
            media_type="application/json",
            size_bytes=len(data),
            checksum=f"sha256:{hashlib.sha256(data).hexdigest()}",
        ),
        owner_type=owner_type,
        owner_id=owner_id,
    )


class _World:
    """One RobotRun with registered Scene and Episode revisions, and a
    Dataset to compose them into, all committed."""

    def __init__(self, unique_id) -> None:
        self.run_id = unique_id("run-compose")
        self.dataset_id = unique_id("ds-compose")
        self.scenes: dict[tuple[str, str], object] = {}
        self.episodes: dict[str, object] = {}

    async def setup(self) -> None:
        async with get_async_sessionmaker()() as session:
            await _seed_run(session, self.run_id)
            await PostgresDatasetRepository(session).create(
                DatasetRecord(dataset_id=self.dataset_id)
            )
            for key in ("a", "b"):
                for channels in (None, ["CAM_FRONT"]):
                    revision = await self._scene(session, key, channels)
                    self.scenes[(key, "all" if channels is None else "front")] = (
                        revision
                    )
            self.episodes["e"] = await self._episode(session, "e")
            await session.commit()

    async def _scene(self, session, key, channels):
        source = recording_source(
            robot_run_id=self.run_id,
            unit_key=f"segment-{key}",
            recording_checksum=RECORDING_CHECKSUM,
        )
        kwargs = {} if channels is None else {"build_config": {"channels": channels}}
        manifest = build_scene_manifest(source=source, **kwargs)
        scene_id = scene_id_for(source)
        checksum = manifest.checksum()
        artifact_id = manifest_artifact_id_for(
            unit_id=scene_id, manifest_checksum=checksum
        )
        await _manifest_artifact(
            session,
            artifact_id=artifact_id,
            kind=ArtifactKind.SCENE_MANIFEST,
            owner_type=ArtifactOwnerType.SCENE,
            owner_id=scene_id,
            data=manifest.to_canonical_bytes(),
        )
        revision = project_scene_revision(
            manifest=manifest,
            manifest_artifact_id=artifact_id,
            manifest_checksum=checksum,
        )
        await PostgresSceneRepository(session).register_revision(revision)
        return revision

    async def _episode(self, session, key):
        manifest = episode_manifest(
            [state("/odom", 1, x=1.0)],
            robot_run_id=self.run_id,
            unit_key=key,
            recording_checksum=RECORDING_CHECKSUM,
        )
        episode_id = episode_id_for(manifest.lineage.source)
        checksum = manifest.checksum()
        artifact_id = manifest_artifact_id_for(
            unit_id=episode_id, manifest_checksum=checksum
        )
        await _manifest_artifact(
            session,
            artifact_id=artifact_id,
            kind=ArtifactKind.EPISODE_MANIFEST,
            owner_type=ArtifactOwnerType.EPISODE,
            owner_id=episode_id,
            data=manifest.to_canonical_bytes(),
        )
        revision = project_episode_revision(
            manifest=manifest,
            manifest_artifact_id=artifact_id,
            manifest_checksum=checksum,
        )
        await PostgresEpisodeRepository(session).register_revision(revision)
        return revision

    def pins(self, *names) -> list[RevisionPin]:
        out = []
        for name in names:
            revision = self.episodes[name] if name == "e" else self.scenes[name]
            out.append(revision.pin())
        return out

    def params(self, version="v1", **members) -> ComposeDatasetVersionJobParams:
        return ComposeDatasetVersionJobParams(
            dataset_id=self.dataset_id, dataset_version=version, **members
        )

    async def compose(self, params, *, context=None):
        async with get_async_sessionmaker()() as session:
            ctx = context(session) if context else _no_store_context(session)
            return await compose_dataset_version(context=ctx, params=params)

    async def rows(self, version="v1"):
        """(version row, sorted member pins) as committed."""
        async with get_async_sessionmaker()() as session:
            row = (
                await session.execute(
                    select(DatasetVersionModel).where(
                        DatasetVersionModel.id == f"{self.dataset_id}:{version}"
                    )
                )
            ).scalar_one_or_none()
            members = (
                await session.execute(
                    select(DatasetVersionMemberModel).where(
                        DatasetVersionMemberModel.dataset_version_id
                        == f"{self.dataset_id}:{version}"
                    )
                )
            ).scalars()
            version = (
                None
                if row is None
                else (
                    row.composition_checksum,
                    row.selector,
                    row.scene_count,
                    row.episode_count,
                    row.created_at,
                )
            )
            return version, sorted((m.unit_id, m.manifest_checksum) for m in members)

    async def version_count(self) -> int:
        async with get_async_sessionmaker()() as session:
            return (
                await session.execute(
                    select(func.count()).where(
                        DatasetVersionModel.dataset_id == self.dataset_id
                    )
                )
            ).scalar_one()


class _NoStore:
    """Stands in for every artifact store of the context: any use fails the
    test, so composition provably makes no object-store call."""

    def __init__(self, name: str, calls: list[str]) -> None:
        self._name, self._calls = name, calls

    def __getattr__(self, attribute: str):
        self._calls.append(f"{self._name}.{attribute}")
        raise AssertionError(f"composition used {self._name}.{attribute}")


OBJECT_STORE_CALLS: list[str] = []
_STORE_FIELDS = (
    "artifact_store",
    "input_store",
    "derived_store",
    "scene_artifact_store",
    "episode_artifact_store",
    "run_artifact_store",
    "analytics_writer",
)


def _no_store_context(session, worker_id: str = "test"):
    context = create_worker_context(session, worker_id=worker_id)
    return dataclasses.replace(
        context, **{f: _NoStore(f, OBJECT_STORE_CALLS) for f in _STORE_FIELDS}
    )


@pytest.fixture()
async def world(unique_id):
    OBJECT_STORE_CALLS.clear()
    world = _World(unique_id)
    await world.setup()
    yield world
    assert OBJECT_STORE_CALLS == []


# --- composition ----------------------------------------------------------------------


async def test_pins_compose_one_complete_version(world):
    pins = world.pins(("b", "all"), "e", ("a", "all"))

    composed = await world.compose(world.params(pins=pins))

    assert composed.created is True
    version = composed.version
    assert version.composition_checksum == composition_checksum(pins)
    assert version.selector is None
    a, b = world.scenes[("a", "all")], world.scenes[("b", "all")]
    assert version.scene.scene_count == 2
    assert version.scene.keyframe_count == a.keyframe_count + b.keyframe_count
    assert version.scene.observation_count == a.observation_count + b.observation_count
    assert version.scene.observed_channels == sorted(
        set(a.observed_channels) | set(b.observed_channels)
    )
    assert version.episode.episode_count == 1
    row, members = await world.rows()
    assert row[0] == version.composition_checksum
    assert members == sorted((p.unit_id, p.manifest_checksum) for p in pins)


async def test_a_selector_is_resolved_once_and_recorded(world):
    front = world.scenes[("a", "front")]
    selector = {
        "sources": [
            {
                "domain": "scene",
                "robot_run_id": world.run_id,
                "producer_fingerprint": front.producer_fingerprint,
            }
        ]
    }

    composed = await world.compose(world.params(selector=selector))

    expected = world.pins(("a", "front"), ("b", "front"))
    assert composed.version.composition_checksum == composition_checksum(expected)
    assert composed.version.selector.model_dump() == selector
    _, members = await world.rows()
    assert members == sorted((p.unit_id, p.manifest_checksum) for p in expected)


async def test_a_mixed_selector_admits_one_revision_per_unit(world):
    """Two fingerprints of the same units select two revisions of each: the
    composition is refused, not resolved to either."""
    clauses = [
        {
            "domain": "scene",
            "robot_run_id": world.run_id,
            "producer_fingerprint": world.scenes[("a", key)].producer_fingerprint,
        }
        for key in ("all", "front")
    ]
    with pytest.raises(CompositionInputError, match="at most one revision per unit"):
        await world.compose(world.params(selector={"sources": clauses}))
    assert await world.version_count() == 0


async def test_a_selector_clause_that_selects_nothing_fails(world):
    selector = {
        "sources": [
            {
                "domain": "episode",
                "robot_run_id": world.run_id,
                "producer_fingerprint": "sha256:" + "9" * 64,
            }
        ]
    }
    with pytest.raises(CompositionInputError, match="selects no registered"):
        await world.compose(world.params(selector=selector))
    assert await world.version_count() == 0


async def test_an_unregistered_last_pin_leaves_no_version(world):
    """Verification happens before any write: a pin that names no
    registered revision, even the last one, rejects the whole composition."""
    unregistered_source = recording_source(
        robot_run_id=world.run_id, unit_key="segment-z"
    )
    unregistered = RevisionPin(
        domain="scene",
        unit_id=scene_id_for(unregistered_source),
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id_for(unregistered_source),
            manifest_checksum="sha256:" + "a" * 64,
        ),
        manifest_checksum="sha256:" + "a" * 64,
    )
    pins = world.pins(("a", "all"), ("b", "all"), "e") + [unregistered]

    with pytest.raises(CompositionInputError, match="no registered revision"):
        await world.compose(world.params(pins=pins))
    assert await world.rows() == (None, [])


async def test_two_revisions_of_one_unit_are_refused(world):
    with pytest.raises(ValueError, match="at most one revision per unit"):
        world.params(pins=world.pins(("a", "all"), ("a", "front")))


async def test_an_unknown_dataset_is_refused(world, unique_id):
    params = ComposeDatasetVersionJobParams(
        dataset_id=unique_id("missing"),
        dataset_version="v1",
        pins=world.pins(("a", "all")),
    )
    with pytest.raises(CompositionInputError, match="Dataset not found"):
        await world.compose(params)


# --- immutability and idempotency -----------------------------------------------------


async def test_the_same_name_and_composition_is_a_no_op(world):
    pins = world.pins(("a", "all"), "e")
    first = await world.compose(world.params(pins=pins))
    before = await world.rows()

    again = await world.compose(world.params(pins=list(reversed(pins))))

    assert (first.created, again.created) == (True, False)
    assert again.version == first.version
    assert await world.rows() == before


async def test_the_same_name_with_another_composition_conflicts(world):
    await world.compose(world.params(pins=world.pins(("a", "all"))))
    before = await world.rows()

    for other in (world.pins(("a", "front")), world.pins(("a", "all"), "e")):
        with pytest.raises(DatasetVersionConflictError, match="never rebound"):
            await world.compose(world.params(pins=other))
    assert await world.rows() == before


async def test_one_revision_is_a_member_of_two_versions(world):
    pins = world.pins(("a", "all"))
    v1 = await world.compose(world.params("v1", pins=pins))
    v2 = await world.compose(world.params("v2", pins=pins + world.pins("e")))

    assert v1.created and v2.created
    assert (await world.rows("v1"))[1] == [(pins[0].unit_id, pins[0].manifest_checksum)]
    assert len((await world.rows("v2"))[1]) == 2
    assert await world.version_count() == 2


async def test_a_crash_before_commit_leaves_no_version(world):
    """The version and member rows are written, then the commit never
    happens: nothing is visible afterwards, and a retry composes it."""

    def crashing(session):
        context = _no_store_context(session)

        class _Crash:
            def __getattr__(self, name):
                return getattr(context, name)

            async def commit(self):
                raise ConnectionError("worker lost its database connection")

        return _Crash()

    params = world.params(pins=world.pins(("a", "all"), "e"))
    with pytest.raises(ConnectionError):
        await world.compose(params, context=crashing)
    assert await world.rows() == (None, [])

    retried = await world.compose(params)
    assert retried.created is True


# --- concurrency -----------------------------------------------------------------------


async def _race(world, first_params, second_params):
    """The second composition reaches its insert while the first holds its
    uncommitted version row; then the first commits. Returns both outcomes
    and whether the second finished before the first committed."""
    first_wrote = asyncio.Event()
    first_committed = asyncio.Event()
    finished_early = False

    def holding(session):
        context = _no_store_context(session)

        class _Holding:
            def __getattr__(self, name):
                return getattr(context, name)

            async def commit(self):
                first_wrote.set()
                await asyncio.sleep(0.5)  # the second blocks on our key meanwhile
                first_committed.set()
                await context.commit()

        return _Holding()

    async def second():
        nonlocal finished_early
        await first_wrote.wait()
        try:
            return await world.compose(second_params)
        except Exception as exc:  # noqa: BLE001 - reported to the test
            return exc
        finally:
            finished_early = not first_committed.is_set()

    one, two = await asyncio.gather(
        world.compose(first_params, context=holding), second()
    )
    return one, two, finished_early


async def test_concurrent_identical_compositions_converge(world):
    params = world.params(pins=world.pins(("a", "all"), ("b", "all")))

    one, two, finished_early = await _race(world, params, params)

    assert not finished_early
    assert (one.created, two.created) == (True, False)
    assert two.version == one.version
    assert await world.version_count() == 1


async def test_concurrent_different_compositions_one_commits(world):
    winner = world.params(pins=world.pins(("a", "all")))
    loser = world.params(pins=world.pins(("b", "all"), "e"))

    one, two, finished_early = await _race(world, winner, loser)

    assert not finished_early
    assert one.created is True
    assert isinstance(two, DatasetVersionConflictError)
    _, members = await world.rows()
    assert members == [
        (p.unit_id, p.manifest_checksum) for p in world.pins(("a", "all"))
    ]


# --- Job recovery ----------------------------------------------------------------------


class _Dispatcher:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    def dispatch_job(self, job_id: str):
        from sceneops_core.executions.schemas import (
            ExecutionBackend,
            ExecutionDispatchResult,
            ExecutionKind,
        )

        self.jobs.append(job_id)
        return ExecutionDispatchResult(
            execution_id=f"exec-{job_id}",
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
        )

    def advance_pipeline(self, pipeline_run_id: str) -> None:
        raise AssertionError("a standalone Job has no pipeline")


async def test_a_reclaimed_job_whose_composition_committed_converges(world, unique_id):
    """The first worker commits the composition and dies before JobRunner
    records success. The lease expires, recovery requeues the Job, and the
    next claim re-runs the handler, which finds the committed composition
    and succeeds without writing."""
    params = world.params(pins=world.pins(("a", "all"), "e"))
    job_id = unique_id("job-compose")
    async with get_async_sessionmaker()() as session:
        jobs = PostgresJobRepository(session)
        await jobs.create(
            JobManifest(
                job_id=job_id,
                type=JobType.COMPOSE_DATASET_VERSION,
                status=JobStatus.QUEUED,
                dataset_id=world.dataset_id,
                dataset_version="v1",
                params=params.model_dump(),
            )
        )
        await jobs.claim_for_run(
            job_id,
            worker_id="celery:dead",
            runnable_statuses={JobStatus.PENDING, JobStatus.QUEUED},
            lease_seconds=60,
        )
        await session.commit()

    # The dead worker's handler got as far as committing the composition.
    first = await world.compose(params)
    assert first.created is True
    committed = await world.rows()

    async with get_async_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        )
        await session.commit()
    dispatcher = _Dispatcher()
    actions = await recover_expired_leases(
        session_factory=get_async_sessionmaker(),
        dispatcher=dispatcher,
        max_actions=10_000,
    )
    assert [a.outcome for a in actions if a.job_id == job_id] == [
        LeaseRecoveryOutcome.REQUEUED
    ]

    async with get_async_sessionmaker()() as session:
        finished = await JobRunner(
            _no_store_context(session, worker_id="celery:second"),
            dispatcher=dispatcher,
        ).run(job_id)

    assert finished.status == JobStatus.SUCCEEDED, finished.error
    result = parse_job_result(JobType.COMPOSE_DATASET_VERSION, finished.result)
    assert result.created is False
    assert result.composition_checksum == first.version.composition_checksum
    assert (result.member_count, result.scene_count, result.episode_count) == (2, 1, 1)
    assert await world.rows() == committed
