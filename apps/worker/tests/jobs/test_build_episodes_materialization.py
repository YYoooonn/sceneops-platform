"""BuildEpisodesJobHandler's recording-materialization boundary
(sceneops_worker.robots.materialization): a local mcap_uri with NO
robot_run_id must behave exactly as before (never touches ArtifactStore,
nothing to verify -- an explicit pinned-path override, no RobotRun
referenced). An ArtifactStore-backed (``s3://``) mcap_uri must be
materialized to a local temp file first. Whenever a robot_run_id IS given
-- local or remote mcap_uri alike -- the RobotRun's own registered
recording ArtifactRecord checksum is now REQUIRED and verified, never
skipped just because no artifact was found (RobotRunNotMaterializedError)
or the URI happened to be local (verify_local_recording_checksum): this
is the RobotRun-registration-boundary invariant closing the domain/
execution-boundary audit's gap, where a bare-path-registered RobotRun
used to be trusted unconditionally. Mirrors test_build_episodes_handler.py's
own MCAP-fixture and mocked-WorkerContext conventions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcap.writer import Writer

from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_worker.episodes.artifacts import EpisodeArtifactWriteResult
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_episodes import BuildEpisodesJobHandler
from sceneops_worker.robots.materialization import (
    MaterializationChecksumError,
    RobotRunNotMaterializedError,
)


def _write_mcap(path: str, messages: list[tuple[str, int, dict, str]]) -> None:
    with open(path, "wb") as f:
        writer = Writer(f)
        writer.start()
        schema_id = writer.register_schema(
            name="sceneops_json", encoding="jsonschema", data=b"{}"
        )
        channel_ids: dict[str, int] = {}
        for topic, log_time, payload, encoding in messages:
            if topic not in channel_ids:
                channel_ids[topic] = writer.register_channel(
                    topic=topic, message_encoding=encoding, schema_id=schema_id
                )
            writer.add_message(
                channel_ids[topic],
                log_time=log_time,
                data=json.dumps(payload).encode(),
                publish_time=log_time,
            )
        writer.finish()


_REMOTE_URI = "s3://sceneops/artifacts/robot_runs/run-1/recording.mcap"


def _robot_run() -> RobotRunRecord:
    return RobotRunRecord(
        run_id="run-1",
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id="art-robotrun-run-1",
        manifest_artifact_id="art-robotrunmanifest-run-1",
        manifest_checksum="sha256:" + "1" * 64,
    )


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _make_context() -> MagicMock:
    context = MagicMock()
    context.default_dataset_id = "d1"
    context.default_dataset_version = "v1"
    context.dataset_store.get_version = AsyncMock(
        return_value=DatasetVersionRecord(dataset_id="d1", version="v1")
    )
    context.episode_artifact_store.write_episode_manifest = AsyncMock(
        side_effect=lambda **kw: EpisodeArtifactWriteResult(
            uri=f"mem://episodes/{kw['episode_id']}.json",
            checksum="sha256:deadbeef",
            size_bytes=123,
        )
    )
    context.artifact_record_store.create = AsyncMock()
    context.commit = AsyncMock()
    return context


def _fixture_mcap_bytes(tmp_path) -> bytes:
    bag_path = str(tmp_path / "source.mcap")
    _write_mcap(
        bag_path,
        [
            (
                "/mission/status",
                1_000_000_000,
                {"mission_id": "mission-1", "operation_state": "running"},
                "json",
            ),
            (
                "/mission/status",
                2_000_000_000,
                {"mission_id": "mission-1", "operation_state": "completed"},
                "json",
            ),
            ("/vehicle/odom", 1_500_000_000, {"position": [1.0, 2.0, 0.0]}, "json"),
        ],
    )
    with open(bag_path, "rb") as f:
        return f.read()


async def test_local_mcap_uri_never_touches_artifact_store(tmp_path) -> None:
    bag_path = str(tmp_path / "run.mcap")
    with open(bag_path, "wb") as f:
        f.write(_fixture_mcap_bytes(tmp_path))

    context = _make_context()
    job = JobManifest(
        job_id="job-1", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", mcap_uri=bag_path
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    result = await BuildEpisodesJobHandler().run(request)

    assert result.episode_count == 1
    context.artifact_store.read_bytes.assert_not_called()


async def test_remote_mcap_uri_materializes_via_artifact_store(tmp_path) -> None:
    data = _fixture_mcap_bytes(tmp_path)
    context = _make_context()
    context.artifact_store.read_bytes = AsyncMock(return_value=data)

    job = JobManifest(
        job_id="job-2", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1",
        dataset_version="v1",
        robot_id="robot-1",
        mcap_uri="s3://sceneops/artifacts/robot_runs/run-1/run-1.mcap",
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    result = await BuildEpisodesJobHandler().run(request)

    assert result.episode_count == 1
    context.artifact_store.read_bytes.assert_awaited_once_with(
        "s3://sceneops/artifacts/robot_runs/run-1/run-1.mcap"
    )


async def test_remote_mcap_uri_verifies_robot_run_artifact_checksum(tmp_path) -> None:
    data = _fixture_mcap_bytes(tmp_path)
    context = _make_context()
    context.artifact_store.read_bytes = AsyncMock(return_value=data)
    context.robot_store.get_run = AsyncMock(return_value=_robot_run())
    matching_artifact = MagicMock(checksum=_sha256(data), uri=_REMOTE_URI)
    context.artifact_record_store.get = AsyncMock(return_value=matching_artifact)

    job = JobManifest(
        job_id="job-3", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", robot_run_id="run-1"
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    result = await BuildEpisodesJobHandler().run(request)

    assert result.episode_count == 1
    assert {c.args for c in context.artifact_record_store.get.await_args_list} == {
        ("art-robotrun-run-1",)
    }


async def test_remote_mcap_uri_with_robot_run_id_missing_artifact_raises(
    tmp_path,
) -> None:
    """A robot_run_id was given, but the recording ArtifactRecord its
    RobotRunRecord references is missing (inconsistent canonical state) --
    this must raise, never silently skip verification."""
    data = _fixture_mcap_bytes(tmp_path)
    context = _make_context()
    context.artifact_store.read_bytes = AsyncMock(return_value=data)
    context.robot_store.get_run = AsyncMock(return_value=_robot_run())
    context.artifact_record_store.get = AsyncMock(return_value=None)

    job = JobManifest(
        job_id="job-remote-unregistered",
        type=JobType.BUILD_EPISODES,
        status=JobStatus.RUNNING,
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", robot_run_id="run-1"
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    with pytest.raises(RobotRunNotMaterializedError, match="run-1"):
        await BuildEpisodesJobHandler().run(request)

    context.episode_artifact_store.write_episode_manifest.assert_not_called()
    context.artifact_record_store.create.assert_not_called()
    context.commit.assert_not_called()


async def test_remote_mcap_uri_checksum_mismatch_raises_before_writing_anything(
    tmp_path,
) -> None:
    data = _fixture_mcap_bytes(tmp_path)
    context = _make_context()
    context.artifact_store.read_bytes = AsyncMock(return_value=data)
    context.robot_store.get_run = AsyncMock(return_value=_robot_run())
    mismatched_artifact = MagicMock(checksum="sha256:" + "0" * 64, uri=_REMOTE_URI)
    context.artifact_record_store.get = AsyncMock(return_value=mismatched_artifact)

    job = JobManifest(
        job_id="job-4", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", robot_run_id="run-1"
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    with pytest.raises(MaterializationChecksumError):
        await BuildEpisodesJobHandler().run(request)

    context.episode_artifact_store.write_episode_manifest.assert_not_called()
    context.artifact_record_store.create.assert_not_called()
    context.commit.assert_not_called()


# ── robot_run_id + local recording URI: the bytes read are verified
#    against the registered recording ArtifactRecord checksum, local or
#    remote alike. ──────────────────────────────────────────────────────


async def test_local_mcap_uri_with_robot_run_id_requires_registered_artifact(
    tmp_path,
) -> None:
    """A robot_run_id whose recording ArtifactRecord is missing must raise
    rather than fall back to any path, exactly like the remote-URI case
    above."""
    bag_path = str(tmp_path / "run.mcap")
    with open(bag_path, "wb") as f:
        f.write(_fixture_mcap_bytes(tmp_path))

    context = _make_context()
    context.robot_store.get_run = AsyncMock(return_value=_robot_run())
    context.artifact_record_store.get = AsyncMock(return_value=None)

    job = JobManifest(
        job_id="job-local-unregistered",
        type=JobType.BUILD_EPISODES,
        status=JobStatus.RUNNING,
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", robot_run_id="run-1"
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    with pytest.raises(RobotRunNotMaterializedError, match="run-1"):
        await BuildEpisodesJobHandler().run(request)

    context.episode_artifact_store.write_episode_manifest.assert_not_called()
    context.artifact_record_store.create.assert_not_called()
    context.commit.assert_not_called()


async def test_local_mcap_uri_with_robot_run_id_and_matching_artifact_succeeds(
    tmp_path,
) -> None:
    """The positive case: a registered recording ArtifactRecord exists for
    this RobotRun and its checksum matches the local file -- Episode
    building proceeds exactly as it already does for the remote-URI case."""
    data = _fixture_mcap_bytes(tmp_path)
    bag_path = str(tmp_path / "run.mcap")
    with open(bag_path, "wb") as f:
        f.write(data)

    context = _make_context()
    context.robot_store.get_run = AsyncMock(return_value=_robot_run())
    matching_artifact = MagicMock(checksum=_sha256(data), uri=bag_path)
    context.artifact_record_store.get = AsyncMock(return_value=matching_artifact)

    job = JobManifest(
        job_id="job-local-registered",
        type=JobType.BUILD_EPISODES,
        status=JobStatus.RUNNING,
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", robot_run_id="run-1"
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    result = await BuildEpisodesJobHandler().run(request)

    assert result.episode_count == 1
    assert {c.args for c in context.artifact_record_store.get.await_args_list} == {
        ("art-robotrun-run-1",)
    }


async def test_local_mcap_uri_with_robot_run_id_checksum_mismatch_raises(
    tmp_path,
) -> None:
    """The registered ArtifactRecord's checksum doesn't match the local
    file's actual bytes -- must refuse to build Episodes from it, exactly
    like the remote-URI mismatch case above."""
    data = _fixture_mcap_bytes(tmp_path)
    bag_path = str(tmp_path / "run.mcap")
    with open(bag_path, "wb") as f:
        f.write(data)

    context = _make_context()
    context.robot_store.get_run = AsyncMock(return_value=_robot_run())
    mismatched_artifact = MagicMock(checksum="sha256:" + "0" * 64, uri=bag_path)
    context.artifact_record_store.get = AsyncMock(return_value=mismatched_artifact)

    job = JobManifest(
        job_id="job-local-mismatch",
        type=JobType.BUILD_EPISODES,
        status=JobStatus.RUNNING,
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1", dataset_version="v1", robot_id="robot-1", robot_run_id="run-1"
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    with pytest.raises(MaterializationChecksumError):
        await BuildEpisodesJobHandler().run(request)

    context.episode_artifact_store.write_episode_manifest.assert_not_called()
    context.artifact_record_store.create.assert_not_called()
    context.commit.assert_not_called()
