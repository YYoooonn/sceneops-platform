"""Phase 6.6 reliability matrix item F (Episode job fails after
materialization) + Episode/Learning retry, against real Postgres +
real MinIO: for a streaming-registered (ArtifactStore-backed) RobotRun,
inject a controlled failure inside Episode building AFTER materialization
has already produced a local temp file, then retry.

Verifies:
  - the canonical MCAP (real MinIO object) is unchanged by the failure
  - the failed attempt's materialized temp file is cleaned up
  - a retry (the existing job re-dispatch semantics -- just calling the
    handler again, no new retry mechanism) succeeds
  - no duplicate canonical state from the failed attempt (it wrote
    nothing -- the failure happens before any Episode manifest/artifact
    write)

Requires SCENEOPS_DATABASE_URL and a reachable MinIO (`make
test-integration` against a running `make local-up` stack). Skips (not
fails) otherwise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_db.postgres.datasets import (
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_episodes import BuildEpisodesJobHandler
from sceneops_worker.robots.registration import register_robot_run_capture

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"


@pytest.mark.usefixtures("cleanup_minio_prefix")
async def test_episode_build_failure_after_materialization_then_retry_succeeds(
    worker_context, unique_id
) -> None:
    robot_id = unique_id("robot")
    robot_run_id = unique_id("run")
    dataset_id = unique_id("dataset")
    dataset_version = "v1"

    # Real Dataset/DatasetVersion (direct repository calls, matching
    # packages/sceneops-db/tests/test_episode_summary_aggregation.py's
    # own setup convention).
    await PostgresDatasetRepository(worker_context.session).create(
        DatasetRecord(dataset_id=dataset_id)
    )
    await PostgresDatasetVersionRepository(worker_context.session).create(
        DatasetVersionRecord(dataset_id=dataset_id, version=dataset_version)
    )
    await worker_context.session.commit()

    # Real streaming-style registration: ArtifactStore-backed RobotRun.
    registration = await register_robot_run_capture(
        context=worker_context,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        mcap_path=_VALID_MCAP,
    )
    original_checksum = registration.artifact.checksum

    def _build_params() -> BuildEpisodesJobParams:
        return BuildEpisodesJobParams(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_id=robot_id,
            robot_run_id=robot_run_id,
        )

    # --- Attempt 1: inject a failure AFTER materialization has already
    # produced and validated the local temp file, but during the actual
    # RosbagAdapter read (extract_episode_source itself). Mirrors "Episode
    # job fails after materialization" (matrix item F) precisely.
    captured_local_paths: list[Path] = []
    real_init = RosbagAdapter.__init__

    def _spy_init(self, *, source_store, source_root_uri):
        captured_local_paths.append(Path(source_root_uri))
        real_init(self, source_store=source_store, source_root_uri=source_root_uri)

    class _InjectedFailure(RuntimeError):
        pass

    def _failing_extract(self, *, robot_id, robot_run_id):
        raise _InjectedFailure(
            "simulated Episode-building failure after materialization"
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(RosbagAdapter, "__init__", _spy_init)
        mp.setattr(RosbagAdapter, "extract_episode_source", _failing_extract)

        job = JobManifest(
            job_id=unique_id("job"),
            type=JobType.BUILD_EPISODES,
            status=JobStatus.RUNNING,
        )
        request = JobHandlerRequest(
            job=job, params=_build_params(), context=worker_context
        )

        with pytest.raises(_InjectedFailure):
            await BuildEpisodesJobHandler().run(request)

    assert len(captured_local_paths) == 1
    materialized_path = captured_local_paths[0]
    # The materialized temp file existed for RosbagAdapter to (attempt to)
    # read, and is cleaned up after the failure -- materialize_recording's
    # own guarantee, exercised here through the real handler, not a fake.
    assert not materialized_path.exists()
    assert not materialized_path.parent.exists()

    # Canonical MCAP unchanged by the failed attempt.
    stored_bytes = await worker_context.robot_run_artifact_store.read_recording_bytes(
        robot_run_id
    )
    assert f"sha256:{hashlib.sha256(stored_bytes).hexdigest()}" == original_checksum

    # No Episode-domain artifact state was written by the failed attempt
    # (BuildEpisodesJobHandler writes EPISODE_MANIFEST ArtifactRecords on
    # success; register_episode -- a separate job -- is what would later
    # create an EpisodeRecord row, out of scope for this handler).
    artifacts_after_failure = await worker_context.artifact_record_store.list(
        dataset_id=dataset_id, dataset_version=dataset_version, limit=10
    )
    assert artifacts_after_failure == []

    # --- Attempt 2: retry -- the SAME job params, no patching, no new
    # retry mechanism, just re-invoking the handler (existing job
    # re-dispatch semantics, e.g. a pipeline's own `force: true`).
    job2 = JobManifest(
        job_id=unique_id("job"), type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    request2 = JobHandlerRequest(
        job=job2, params=_build_params(), context=worker_context
    )

    result = await BuildEpisodesJobHandler().run(request2)

    assert result.episode_count >= 1

    # The retry produced exactly its own episode manifests -- not double
    # the expected count (no leftover/duplicate state from the failed
    # first attempt, which wrote nothing).
    artifacts_after_retry = await worker_context.artifact_record_store.list(
        dataset_id=dataset_id, dataset_version=dataset_version, limit=10
    )
    assert len(artifacts_after_retry) == result.episode_count

    # Still unchanged after a successful retry too.
    stored_bytes_after_retry = (
        await worker_context.robot_run_artifact_store.read_recording_bytes(robot_run_id)
    )
    assert (
        f"sha256:{hashlib.sha256(stored_bytes_after_retry).hexdigest()}"
        == original_checksum
    )
