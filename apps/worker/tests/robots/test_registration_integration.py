"""Real-Postgres + real-MinIO integration coverage for
register_robot_run_capture -- idempotency/conflict cases end-to-end
against the real repositories and the real S3ArtifactStore, plus a real
concurrency test proving two independent concurrent registrations of the
same robot_run_id converge on exactly one RobotRun / one ArtifactRecord /
one physical object, using the artifacts/robot_runs tables' own primary
key constraints -- no process-local or Redis lock.

Requires SCENEOPS_DATABASE_URL and a reachable MinIO (`make test-integration`
against a running `make local-up` stack). Skips (not fails) otherwise.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sceneops_core.artifacts.schemas import ArtifactOwnerType
from sceneops_db.postgres.robots import PostgresRobotRunRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.robots.registration import (
    RobotRunRegistrationConflictError,
    register_robot_run_capture,
)
from sceneops_worker.stores.artifacts import ArtifactRecordStore

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"
_OTHER_MCAP = _FIXTURES_DIR / "nav_msgs_odometry.mcap"


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_first_registration_against_real_infra(worker_context, unique_id) -> None:
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")

    registration = await register_robot_run_capture(
        context=worker_context,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        mcap_path=_VALID_MCAP,
    )

    assert registration.created is True
    assert await worker_context.robot_run_artifact_store.exists(robot_run_id)
    stored = await worker_context.robot_run_artifact_store.read_recording_bytes(
        robot_run_id
    )
    assert stored == _VALID_MCAP.read_bytes()


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_exact_retry_against_real_infra_creates_nothing_new(
    worker_context, unique_id
) -> None:
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")

    first = await register_robot_run_capture(
        context=worker_context,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        mcap_path=_VALID_MCAP,
    )
    second = await register_robot_run_capture(
        context=worker_context,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        mcap_path=_VALID_MCAP,
    )

    assert second.created is False
    assert second.robot_run.run_id == first.robot_run.run_id
    assert second.artifact.artifact_id == first.artifact.artifact_id


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_same_robot_run_id_different_checksum_conflicts(
    worker_context, unique_id
) -> None:
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")

    await register_robot_run_capture(
        context=worker_context,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        mcap_path=_VALID_MCAP,
    )

    with pytest.raises(RobotRunRegistrationConflictError):
        await register_robot_run_capture(
            context=worker_context,
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            mcap_path=_OTHER_MCAP,
        )

    # Original state must be untouched by the refused conflicting attempt.
    unchanged = await worker_context.robot_run_artifact_store.read_recording_bytes(
        robot_run_id
    )
    assert unchanged == _VALID_MCAP.read_bytes()


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_orphaned_object_reuse_against_real_infra(
    worker_context, unique_id
) -> None:
    """Simulates "ArtifactStore upload succeeded, DB registration failed"
    directly against real MinIO: the object exists at the deterministic
    key with no RobotRun/ArtifactRecord in Postgres yet. A retry must
    reuse it (no re-upload, no duplicate object) and complete DB
    registration."""
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")

    await worker_context.robot_run_artifact_store.write_recording(
        robot_run_id=robot_run_id, data=_VALID_MCAP.read_bytes()
    )
    existing_run = await worker_context.robot_store.get_run(robot_run_id)
    assert existing_run is None  # confirms the DB side really is absent

    registration = await register_robot_run_capture(
        context=worker_context,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        mcap_path=_VALID_MCAP,
    )

    assert registration.created is True
    assert registration.robot_run.run_id == robot_run_id


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_existing_object_key_different_checksum_never_overwrites(
    worker_context, unique_id
) -> None:
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")

    await worker_context.robot_run_artifact_store.write_recording(
        robot_run_id=robot_run_id, data=_OTHER_MCAP.read_bytes()
    )

    with pytest.raises(
        RobotRunRegistrationConflictError, match="refusing to overwrite"
    ):
        await register_robot_run_capture(
            context=worker_context,
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            mcap_path=_VALID_MCAP,
        )

    # The pre-existing (different) object must be untouched.
    stored = await worker_context.robot_run_artifact_store.read_recording_bytes(
        robot_run_id
    )
    assert stored == _OTHER_MCAP.read_bytes()
    assert await worker_context.robot_store.get_run(robot_run_id) is None


@pytest.mark.usefixtures(
    "cleanup_minio_prefix", "_fresh_database_connection", "_minio_reachable"
)
async def test_concurrent_registrations_converge_on_one_robot_run_and_one_artifact(
    worker_settings, unique_id
) -> None:
    """Two independent, truly concurrent registration attempts for the
    SAME robot_run_id + SAME checksum -- real Postgres, forced
    interleaving via asyncio.Barrier (mirrors
    packages/sceneops-db/tests/test_episode_summary_aggregation.py's own
    concurrency test pattern). Relies entirely on robots.robot_id /
    artifacts.artifact_id / robot_runs.run_id being real primary keys --
    no process-local or Redis lock anywhere in this path."""
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")
    barrier = asyncio.Barrier(2)

    async def _attempt():
        sessionmaker = get_async_sessionmaker()
        async with sessionmaker() as session:
            context = create_worker_context(
                session, settings=worker_settings, worker_id="concurrent-test"
            )
            await barrier.wait()
            return await register_robot_run_capture(
                context=context,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                mcap_path=_VALID_MCAP,
            )

    dependencies_module._artifact_store = None
    dependencies_module._raw_source_store = None
    try:
        result_a, result_b = await asyncio.gather(_attempt(), _attempt())
    finally:
        store_settings = worker_settings.artifact
        dependencies_module._artifact_store = None
        dependencies_module._raw_source_store = None

    assert result_a.robot_run.run_id == robot_run_id
    assert result_b.robot_run.run_id == robot_run_id
    assert result_a.artifact.artifact_id == result_b.artifact.artifact_id
    assert result_a.artifact.checksum == result_b.artifact.checksum
    assert result_a.created or result_b.created

    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as verify_session:
        run_repo = PostgresRobotRunRepository(verify_session)
        runs = await run_repo.list(robot_id=robot_id, limit=10)
        assert len(runs) == 1

        artifact_store = ArtifactRecordStore(verify_session)
        artifacts = await artifact_store.list(
            owner_type=ArtifactOwnerType.ROBOT_RUN, owner_id=robot_run_id, limit=10
        )
        assert len(artifacts) == 1

    from sceneops_storage.backends.s3 import S3ArtifactStore

    cleanup_store = S3ArtifactStore(settings=store_settings)
    try:
        assert await cleanup_store.exists(runs[0].mcap_uri)
    finally:
        await cleanup_store.delete_prefix(worker_settings.artifact_root_uri)
