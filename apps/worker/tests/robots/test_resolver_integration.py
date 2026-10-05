"""Real Postgres + real MinIO coverage for the verified recording resolver
(sceneops_worker.robots.resolver) and its consumers.

    publish (DB-free Recording Publisher) -> REGISTER_ROBOT_RUN
      -> resolve_recording(robot_run_id) -> consumer

Every RobotRun here is MinIO-backed and referenced by ``robot_run_id`` only;
no test passes a recording URI or local path to a consumer. BUILD_EPISODES
over the same path, including cleanup after a failed attempt, is covered by
test_episode_retry_integration.py.

Requires SCENEOPS_DATABASE_URL and a reachable MinIO (`make
test-integration` against a running `make local-up` stack). Skips (not
fails) otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from sceneops_core.jobs.schemas import (
    IngestRobotStatesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.robots.telemetry import RecordingTelemetryReader
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.robots.ingest_robot_states import (
    IngestRobotStatesJobHandler,
)
from sceneops_worker.robots.registration import register_robot_run
from sceneops_worker.robots.resolver import (
    RecordingBytesMissingError,
    RecordingIntegrityError,
    resolve_recording,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _resolve(worker_context, robot_run_id: str):
    return resolve_recording(
        robot_run_id=robot_run_id,
        robot_store=worker_context.robot_store,
        artifact_record_store=worker_context.artifact_record_store,
        artifact_store=worker_context.artifact_store,
    )


async def _publish_and_register(worker_context, publish, unique_id):
    robot_id = unique_id("robot")
    run_id = unique_id("run")
    publication = await publish(_VALID_MCAP, run_id=run_id, robot_id=robot_id)
    registration = await register_robot_run(
        context=worker_context, manifest_uri=publication.manifest_uri
    )
    return robot_id, run_id, registration


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_resolves_registered_minio_recording_to_verified_local_copy(
    worker_context, publish, unique_id
) -> None:
    _, run_id, registration = await _publish_and_register(
        worker_context, publish, unique_id
    )
    recording_artifact = registration.recording_artifact
    assert recording_artifact.uri.startswith("s3://")

    async with _resolve(worker_context, run_id) as recording:
        assert recording.robot_run_id == run_id
        assert recording.robot_id == registration.robot_run.robot_id
        assert recording.artifact_id == registration.robot_run.recording_artifact_id
        assert recording.checksum == recording_artifact.checksum
        assert recording.size_bytes == recording_artifact.size_bytes
        assert recording.recording_format == "mcap"
        assert recording.source_clock == "mcap_log_time"
        assert recording.local_path.read_bytes() == _VALID_MCAP.read_bytes()
        # The copy is a real, readable MCAP for the existing reader.
        robot_states = RecordingTelemetryReader(
            recording_path=str(recording.local_path)
        ).extract_robot_states(robot_id="robot", robot_run_id=run_id)
        assert robot_states
        local_path = recording.local_path

    assert not local_path.exists()
    assert not local_path.parent.exists()

    # Read-only: the canonical object is unchanged.
    stored = await worker_context.artifact_store.read_bytes(recording_artifact.uri)
    assert _sha256(stored) == recording_artifact.checksum


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_concurrent_resolutions_of_same_run_do_not_collide(
    worker_context, worker_settings, publish, unique_id
) -> None:
    """Two independent consumers (each with its own DB session, as two job
    executions would have) resolving the same RobotRun at the same time."""
    _, run_id, registration = await _publish_and_register(
        worker_context, publish, unique_id
    )
    barrier = asyncio.Barrier(2)

    async def _consume() -> Path:
        async with get_async_sessionmaker()() as session:
            context = create_worker_context(
                session, settings=worker_settings, worker_id="test-concurrent"
            )
            async with _resolve(context, run_id) as recording:
                await barrier.wait()  # force real overlap between the two copies
                assert _sha256(recording.local_path.read_bytes()) == (
                    registration.recording_artifact.checksum
                )
                return recording.local_path

    path_a, path_b = await asyncio.gather(_consume(), _consume())

    assert path_a.parent != path_b.parent
    assert not path_a.exists()
    assert not path_b.exists()


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_tampered_minio_object_fails_checksum_verification(
    worker_context, publish, unique_id
) -> None:
    _, run_id, registration = await _publish_and_register(
        worker_context, publish, unique_id
    )
    uri = registration.recording_artifact.uri
    # Simulate a write-once violation after registration: same size,
    # different bytes -- only the sha256 check can catch it.
    original = await worker_context.artifact_store.read_bytes(uri)
    tampered = original[:-1] + bytes([original[-1] ^ 0xFF])
    await worker_context.artifact_store.write_bytes(uri, tampered)

    with pytest.raises(RecordingIntegrityError, match="checksum mismatch"):
        async with _resolve(worker_context, run_id):
            pytest.fail("must not yield unverified bytes")


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_deleted_minio_object_fails_as_missing_bytes(
    worker_context, publish, unique_id
) -> None:
    _, run_id, registration = await _publish_and_register(
        worker_context, publish, unique_id
    )
    await worker_context.artifact_store.delete_prefix(
        registration.recording_artifact.uri
    )

    with pytest.raises(RecordingBytesMissingError):
        async with _resolve(worker_context, run_id):
            pytest.fail("must not yield")


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_ingest_robot_states_consumes_minio_backed_run_by_id(
    worker_context, publish, unique_id
) -> None:
    robot_id, run_id, _ = await _publish_and_register(
        worker_context, publish, unique_id
    )
    request = JobHandlerRequest(
        job=JobManifest(
            job_id=unique_id("job"),
            type=JobType.INGEST_ROBOT_STATES,
            status=JobStatus.RUNNING,
        ),
        params=IngestRobotStatesJobParams(robot_run_id=run_id),
        context=worker_context,
    )

    result = await IngestRobotStatesJobHandler().run(request)

    assert result.robot_run_id == run_id
    assert result.robot_id == robot_id  # the RobotRun's robot, not a caller's
    assert result.state_count > 0
    assert result.mission_count > 0
    stored_states = await worker_context.robot_store.list_states(
        robot_run_id=run_id, limit=result.state_count + 1
    )
    assert len(stored_states) == result.state_count
    assert {state.robot_id for state in stored_states} == {robot_id}
