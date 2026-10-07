"""Recording Publisher + REGISTER_ROBOT_RUN against real MinIO and real
Postgres: write-once publication semantics on S3, verification of the
published bytes, single-transaction registration, unique-constraint races
between concurrent registrations, and the row-locked robot platform rule.

Requires SCENEOPS_DATABASE_URL (a database migrated to head) and a
reachable MinIO (`make test-integration` against `make local-up`). Skips
(not fails) otherwise.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sceneops_core.artifacts.schemas import ArtifactOwnerType
from sceneops_core.robots.manifest import load_canonical_robot_run_manifest
from sceneops_db.postgres.robots import (
    PostgresRobotRepository,
    PostgresRobotRunRepository,
)
from sceneops_db.session import get_async_sessionmaker
from sceneops_integrations.recording import RecordingPublicationConflictError
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.robots.registration import (
    RecordingVerificationError,
    RobotPlatformConflictError,
    RobotRunRegistrationConflictError,
    register_robot_run,
)
from sceneops_worker.stores.artifacts import ArtifactRecordStore

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"
_OTHER_MCAP = _FIXTURES_DIR / "nav_msgs_odometry.mcap"


async def _state(robot_id: str, run_id: str) -> tuple[list, list]:
    """Committed canonical state, read through a fresh session."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        runs = await PostgresRobotRunRepository(session).list(robot_id=robot_id)
        artifacts = await ArtifactRecordStore(session).list(
            owner_type=ArtifactOwnerType.ROBOT_RUN, owner_id=run_id, limit=10
        )
    return runs, artifacts


async def test_publish_and_register_against_real_infra(
    worker_context, worker_settings, publish, unique_id
) -> None:
    robot_id, run_id = unique_id("robot"), unique_id("run")
    publication = await publish(
        _VALID_MCAP, run_id=run_id, robot_id=robot_id, robot_platform="replay"
    )

    s3 = S3ArtifactStore(settings=worker_settings.artifact)
    assert await s3.read_bytes(publication.recording_uri) == _VALID_MCAP.read_bytes()
    manifest_bytes = await s3.read_bytes(publication.manifest_uri)
    assert load_canonical_robot_run_manifest(manifest_bytes) == publication.manifest

    registration = await register_robot_run(
        context=worker_context, manifest_uri=publication.manifest_uri, job_id="job-x"
    )
    assert registration.created is True

    runs, artifacts = await _state(robot_id, run_id)
    assert [r.run_id for r in runs] == [run_id]
    run = runs[0]
    assert run.manifest_checksum == publication.manifest_checksum
    assert run.registered_at is not None
    by_kind = {a.kind: a for a in artifacts}
    assert set(by_kind) == {"robot_run_recording", "robot_run_manifest"}
    assert by_kind["robot_run_recording"].uri == publication.recording_uri
    assert (
        by_kind["robot_run_recording"].checksum
        == publication.manifest.recording.checksum
    )
    assert by_kind["robot_run_manifest"].uri == publication.manifest_uri
    assert by_kind["robot_run_manifest"].job_id == "job-x"


async def test_identical_republish_and_reregister_converge(
    worker_context, worker_settings, publish, unique_id
) -> None:
    robot_id, run_id = unique_id("robot"), unique_id("run")
    first = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)
    await register_robot_run(context=worker_context, manifest_uri=first.manifest_uri)

    again = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)
    assert not again.recording_written and not again.manifest_written
    second = await register_robot_run(
        context=worker_context, manifest_uri=again.manifest_uri
    )
    assert second.created is False

    runs, artifacts = await _state(robot_id, run_id)
    assert len(runs) == 1 and len(artifacts) == 2


async def test_conflicting_republish_is_refused_on_real_minio(
    worker_settings, publish, unique_id, _minio_reachable
) -> None:
    robot_id, run_id = unique_id("robot"), unique_id("run")
    first = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)

    with pytest.raises(RecordingPublicationConflictError):
        await publish(_OTHER_MCAP, run_id=run_id, robot_id=robot_id)

    s3 = S3ArtifactStore(settings=worker_settings.artifact)
    assert await s3.read_bytes(first.recording_uri) == _VALID_MCAP.read_bytes()
    assert await s3.read_bytes(first.manifest_uri) == (
        first.manifest.to_canonical_bytes()
    )


async def test_different_manifest_for_registered_run_is_hard_conflict(
    worker_context, worker_settings, publish, unique_id
) -> None:
    robot_id, run_id = unique_id("robot"), unique_id("run")
    first = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)
    await register_robot_run(context=worker_context, manifest_uri=first.manifest_uri)

    # A second, valid publication of different bytes for the same run_id
    # under another root (the per-root key cannot be overwritten).
    other_settings = worker_settings.model_copy(deep=True)
    other_settings.artifact.root_uri = worker_settings.artifact.root_uri + "-other"
    from sceneops_core.robots.manifest import CaptureSource, CaptureSourceKind
    from sceneops_integrations.recording import publish_recording

    other = await publish_recording(
        artifact_store=S3ArtifactStore(settings=other_settings.artifact),
        root_uri=other_settings.artifact.robot_run_root_uri,
        recording_path=_OTHER_MCAP,
        run_id=run_id,
        robot_id=robot_id,
        robot_platform=None,
        capture_source=CaptureSource(kind=CaptureSourceKind.FILE),
        source_clock="mcap_log_time",
    )
    with pytest.raises(RobotRunRegistrationConflictError):
        await register_robot_run(
            context=worker_context, manifest_uri=other.manifest_uri
        )

    runs, _ = await _state(robot_id, run_id)
    assert [r.manifest_checksum for r in runs] == [first.manifest_checksum]


async def test_tampered_recording_on_minio_is_rejected(
    worker_context, worker_settings, publish, unique_id
) -> None:
    robot_id, run_id = unique_id("robot"), unique_id("run")
    publication = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)
    data = bytearray(_VALID_MCAP.read_bytes())
    data[-1] ^= 0xFF
    await S3ArtifactStore(settings=worker_settings.artifact).write_bytes(
        publication.recording_uri, bytes(data)
    )

    with pytest.raises(RecordingVerificationError, match="checksum"):
        await register_robot_run(
            context=worker_context, manifest_uri=publication.manifest_uri
        )

    runs, artifacts = await _state(robot_id, run_id)
    assert runs == [] and artifacts == []
    async with get_async_sessionmaker()() as session:
        assert await PostgresRobotRepository(session).get(robot_id) is None


async def test_failure_inside_transaction_leaves_no_partial_rows(
    worker_context, publish, unique_id, monkeypatch
) -> None:
    """Both ArtifactRecords are flushed to real Postgres before the
    RobotRun insert fails; nothing may survive the rollback."""
    robot_id, run_id = unique_id("robot"), unique_id("run")
    publication = await publish(
        _VALID_MCAP, run_id=run_id, robot_id=robot_id, robot_platform="replay"
    )

    async def _fail(_run):
        raise RuntimeError("injected failure before RobotRun insert")

    monkeypatch.setattr(worker_context.robot_store, "create_run", _fail)
    with pytest.raises(RuntimeError, match="injected"):
        await register_robot_run(
            context=worker_context, manifest_uri=publication.manifest_uri
        )

    runs, artifacts = await _state(robot_id, run_id)
    assert runs == [] and artifacts == []
    async with get_async_sessionmaker()() as session:
        assert await PostgresRobotRepository(session).get(robot_id) is None


async def _concurrently(worker_settings, *coroutine_factories):
    barrier = asyncio.Barrier(len(coroutine_factories))

    async def _attempt(factory):
        async with get_async_sessionmaker()() as session:
            context = create_worker_context(
                session, settings=worker_settings, worker_id="concurrent-test"
            )
            await barrier.wait()
            return await factory(context)

    dependencies_module._artifact_store = None
    dependencies_module._input_store = None
    try:
        return await asyncio.gather(
            *(_attempt(f) for f in coroutine_factories), return_exceptions=True
        )
    finally:
        dependencies_module._artifact_store = None
        dependencies_module._input_store = None


@pytest.mark.usefixtures("_fresh_database_connection", "_minio_reachable")
async def test_concurrent_registration_of_same_manifest_converges(
    worker_settings, publish, unique_id
) -> None:
    robot_id, run_id = unique_id("robot"), unique_id("run")
    publication = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)

    def _register(context):
        return register_robot_run(
            context=context, manifest_uri=publication.manifest_uri
        )

    results = await _concurrently(worker_settings, _register, _register)

    assert not any(isinstance(r, BaseException) for r in results), results
    assert sorted(r.created for r in results) == [False, True]
    assert results[0].robot_run == results[1].robot_run

    runs, artifacts = await _state(robot_id, run_id)
    assert len(runs) == 1 and len(artifacts) == 2


@pytest.mark.usefixtures("_fresh_database_connection", "_minio_reachable")
async def test_concurrent_platform_fill_for_same_robot_has_one_winner(
    worker_settings, publish, unique_id
) -> None:
    """Two runs of the same new robot assert different platforms
    concurrently. The Robot row is created-if-absent and then locked, so
    exactly one platform is set and the other registration fails."""
    robot_id = unique_id("robot")
    run_a, run_b = unique_id("run"), unique_id("run")
    pub_a = await publish(
        _VALID_MCAP, run_id=run_a, robot_id=robot_id, robot_platform="platform-a"
    )
    pub_b = await publish(
        _OTHER_MCAP, run_id=run_b, robot_id=robot_id, robot_platform="platform-b"
    )

    results = await _concurrently(
        worker_settings,
        lambda ctx: register_robot_run(context=ctx, manifest_uri=pub_a.manifest_uri),
        lambda ctx: register_robot_run(context=ctx, manifest_uri=pub_b.manifest_uri),
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    successes = [r for r in results if not isinstance(r, BaseException)]
    assert len(successes) == 1 and len(failures) == 1, results
    assert isinstance(failures[0], RobotPlatformConflictError)

    winner = successes[0].robot_run
    expected_platform = "platform-a" if winner.run_id == run_a else "platform-b"
    async with get_async_sessionmaker()() as session:
        robot = await PostgresRobotRepository(session).get(robot_id)
        runs = await PostgresRobotRunRepository(session).list(robot_id=robot_id)
    assert robot.platform == expected_platform
    assert [r.run_id for r in runs] == [winner.run_id]
    _, loser_artifacts = await _state(
        robot_id, run_b if winner.run_id == run_a else run_a
    )
    assert loser_artifacts == []


async def test_platform_fill_once_then_conflict_on_real_postgres(
    worker_context, publish, unique_id
) -> None:
    robot_id = unique_id("robot")
    run_1, run_2, run_3 = (unique_id("run") for _ in range(3))

    # absent + null -> created with null platform
    p1 = await publish(_VALID_MCAP, run_id=run_1, robot_id=robot_id)
    await register_robot_run(context=worker_context, manifest_uri=p1.manifest_uri)
    # exists with null + given -> fill once
    p2 = await publish(
        _OTHER_MCAP, run_id=run_2, robot_id=robot_id, robot_platform="replay"
    )
    await register_robot_run(context=worker_context, manifest_uri=p2.manifest_uri)
    # exists with value + different -> conflict
    p3 = await publish(
        _VALID_MCAP, run_id=run_3, robot_id=robot_id, robot_platform="other"
    )
    with pytest.raises(RobotPlatformConflictError):
        await register_robot_run(context=worker_context, manifest_uri=p3.manifest_uri)

    async with get_async_sessionmaker()() as session:
        robot = await PostgresRobotRepository(session).get(robot_id)
        runs = await PostgresRobotRunRepository(session).list(robot_id=robot_id)
    assert robot.platform == "replay"
    assert sorted(r.run_id for r in runs) == sorted([run_1, run_2])


async def test_register_job_is_independent_of_job_dataset_envelope(
    worker_context, publish, unique_id
) -> None:
    """The persisted Job path POST /robot-runs:register feeds (params as
    JobService stores them, handler registry, steps, typed result), run
    twice for the same manifest under two different job dataset contexts.

    The shared Job envelope carries dataset_id/dataset_version (job row,
    injected params, execution key); RobotRun registration must not depend
    on them: same RobotRun, idempotent second run, no DatasetVersion scope
    on any record."""
    from sceneops_core.common.time import utc_now
    from sceneops_core.jobs.schemas import (
        JobManifest,
        JobStatus,
        JobType,
        create_initial_job_steps,
        parse_job_params,
    )
    from sceneops_worker.jobs.runner import JobRunner

    robot_id, run_id = unique_id("robot"), unique_id("run")
    publication = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)

    async def _run_job(dataset_id: str, dataset_version: str):
        params = parse_job_params(
            JobType.REGISTER_ROBOT_RUN,
            {
                "manifest_uri": publication.manifest_uri,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
            },
        ).model_dump()
        assert "dataset_id" not in params
        now = utc_now()
        job = await worker_context.job_store.create(
            JobManifest(
                job_id=unique_id("job"),
                type=JobType.REGISTER_ROBOT_RUN,
                status=JobStatus.PENDING,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                params=params,
                steps=create_initial_job_steps(JobType.REGISTER_ROBOT_RUN),
                execution_key=unique_id("key"),
                queued_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await worker_context.commit()
        # A standalone Job: it belongs to no pipeline, so nothing is dispatched.
        runner = JobRunner(worker_context, dispatcher=MagicMock())
        return job, await runner.run(job.job_id)

    first_job, first = await _run_job("default", "v0")
    runs_after_first, artifacts_after_first = await _state(robot_id, run_id)
    second_job, second = await _run_job("other-dataset", "v9")

    assert first.status == second.status == JobStatus.SUCCEEDED
    assert first.result["created"] is True
    assert second.result["created"] is False
    domain_keys = (
        "run_id",
        "robot_id",
        "recording_artifact_id",
        "manifest_artifact_id",
        "manifest_checksum",
    )
    assert {k: first.result[k] for k in domain_keys} == {
        k: second.result[k] for k in domain_keys
    }
    assert first.result["manifest_checksum"] == publication.manifest_checksum

    runs, artifacts = await _state(robot_id, run_id)
    assert runs == runs_after_first and len(runs) == 1
    assert artifacts == artifacts_after_first and len(artifacts) == 2
    for artifact in artifacts:
        assert (artifact.dataset_id, artifact.dataset_version) == (None, None)
        # job_id = the registering Job; the idempotent second Job records nothing.
        assert artifact.job_id == first_job.job_id
