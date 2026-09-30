"""BuildEpisodesJobHandler's recording-materialization boundary
(sceneops_worker.robots.materialization): a local mcap_uri must behave
exactly as before (never touches ArtifactStore); an ArtifactStore-backed
(``s3://``) mcap_uri must be materialized to a local temp file first,
with the RobotRun's own registered ArtifactRecord checksum verified when
resolvable. Mirrors test_build_episodes_handler.py's own MCAP-fixture and
mocked-WorkerContext conventions.
"""

from __future__ import annotations

import hashlib
import json
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
from sceneops_worker.robots.materialization import MaterializationChecksumError


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
    context.robot_store.get_run = AsyncMock(
        return_value=RobotRunRecord(
            run_id="run-1",
            robot_id="robot-1",
            mcap_uri="s3://sceneops/artifacts/robot_runs/run-1/run-1.mcap",
        )
    )
    context.robot_store.save_run = AsyncMock()
    matching_artifact = MagicMock(checksum=_sha256(data))
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
    context.artifact_record_store.get.assert_awaited_once_with("art-robotrun-run-1")


async def test_remote_mcap_uri_checksum_mismatch_raises_before_writing_anything(
    tmp_path,
) -> None:
    data = _fixture_mcap_bytes(tmp_path)
    context = _make_context()
    context.artifact_store.read_bytes = AsyncMock(return_value=data)
    context.robot_store.get_run = AsyncMock(
        return_value=RobotRunRecord(
            run_id="run-1",
            robot_id="robot-1",
            mcap_uri="s3://sceneops/artifacts/robot_runs/run-1/run-1.mcap",
        )
    )
    mismatched_artifact = MagicMock(checksum="sha256:" + "0" * 64)
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
