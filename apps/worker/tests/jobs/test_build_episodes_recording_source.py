"""BuildEpisodesJobHandler's recording source: the RobotRun named by
``robot_run_id``, read only through the verified recording resolver
(sceneops_worker.robots.resolver). No caller-supplied recording URI exists
on the job contract, and resolver failures stop the job before any Episode
manifest or ArtifactRecord is written. Mirrors test_build_episodes_handler.py's
MCAP-fixture and mocked-WorkerContext conventions; the recording itself lives
in a real LocalArtifactStore (``register_local_recording``).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcap.writer import Writer
from pydantic import ValidationError

from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.episodes.artifacts import EpisodeArtifactWriteResult
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_episodes import BuildEpisodesJobHandler
from sceneops_worker.robots.resolver import (
    RecordingIntegrityError,
    RobotRunNotFoundError,
)


def _write_mcap(path: Path, messages: list[tuple[str, int, dict, str]]) -> None:
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


def _fixture_mcap_bytes(tmp_path: Path) -> bytes:
    bag_path = tmp_path / "source.mcap"
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
    return bag_path.read_bytes()


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


def _request(context: MagicMock, *, robot_run_id: str = "run-1") -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-1", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1",
        dataset_version="v1",
        robot_run_id=robot_run_id,
    )
    return JobHandlerRequest(job=job, params=params, context=context)


def _assert_nothing_written(context: MagicMock) -> None:
    context.episode_artifact_store.write_episode_manifest.assert_not_called()
    context.artifact_record_store.create.assert_not_called()
    context.commit.assert_not_called()


async def test_builds_from_resolved_recording_copy(
    tmp_path, register_local_recording, monkeypatch
) -> None:
    context = _make_context()
    robot_run, artifact = await register_local_recording(
        context, _fixture_mcap_bytes(tmp_path)
    )
    adapter_paths: list[str] = []
    real_init = RosbagAdapter.__init__

    def _spy_init(self, **kwargs):
        adapter_paths.append(kwargs["source_root_uri"])
        real_init(self, **kwargs)

    monkeypatch.setattr(RosbagAdapter, "__init__", _spy_init)

    result = await BuildEpisodesJobHandler().run(_request(context))

    assert result.episode_count == 1
    context.robot_store.get_run.assert_awaited_once_with(robot_run.run_id)
    context.artifact_record_store.get.assert_awaited_once_with(artifact.artifact_id)
    context.artifact_store.read_bytes.assert_awaited_once_with(artifact.uri)
    # RosbagAdapter read the resolver's private copy -- not the stored
    # artifact path -- and that copy is gone once the job is done.
    assert len(adapter_paths) == 1
    assert adapter_paths[0] != artifact.uri
    assert not Path(adapter_paths[0]).exists()


async def test_episodes_belong_to_the_robot_run_s_robot(
    tmp_path, register_local_recording
) -> None:
    context = _make_context()
    await register_local_recording(
        context, _fixture_mcap_bytes(tmp_path), robot_id="robot-of-run"
    )

    result = await BuildEpisodesJobHandler().run(_request(context))

    assert result.episode_count == 1
    manifests = [
        call.kwargs["manifest"]
        for call in context.episode_artifact_store.write_episode_manifest.await_args_list
    ]
    assert [m.lineage.robot_id for m in manifests] == ["robot-of-run"]
    assert {m.lineage.robot_run_id for m in manifests} == {"run-1"}


async def test_missing_robot_run_fails_before_writing_anything(
    tmp_path, register_local_recording
) -> None:
    context = _make_context()
    await register_local_recording(context, _fixture_mcap_bytes(tmp_path))

    with pytest.raises(RobotRunNotFoundError, match="run-unknown"):
        await BuildEpisodesJobHandler().run(
            _request(context, robot_run_id="run-unknown")
        )

    context.artifact_store.read_bytes.assert_not_called()
    _assert_nothing_written(context)


async def test_checksum_mismatch_fails_before_writing_anything(
    tmp_path, register_local_recording
) -> None:
    context = _make_context()
    _, artifact = await register_local_recording(context, _fixture_mcap_bytes(tmp_path))
    # Same size, different bytes: only the sha256 check can catch it.
    stored = Path(artifact.uri)
    data = bytearray(stored.read_bytes())
    data[-1] ^= 0xFF
    stored.write_bytes(bytes(data))

    with pytest.raises(RecordingIntegrityError, match="checksum mismatch"):
        await BuildEpisodesJobHandler().run(_request(context))

    _assert_nothing_written(context)


# ── job contract: robot_run_id is the only recording source ────────────────


def test_params_have_no_recording_uri_or_robot_fields() -> None:
    assert not {"mcap_uri", "rosbag_uri", "robot_id"} & set(
        BuildEpisodesJobParams.model_fields
    )


@pytest.mark.parametrize("field", ["mcap_uri", "rosbag_uri", "mcapUri"])
def test_params_reject_recording_uri(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        BuildEpisodesJobParams.model_validate(
            {
                "dataset_id": "d1",
                "dataset_version": "v1",
                "robot_run_id": "run-1",
                field: "/data/raw/rosbag/run.mcap",
            }
        )


@pytest.mark.parametrize("robot_run_id", [None, ""])
def test_params_require_robot_run_id(robot_run_id) -> None:
    payload = {"dataset_id": "d1", "dataset_version": "v1"}
    if robot_run_id is not None:
        payload["robot_run_id"] = robot_run_id
    with pytest.raises(ValidationError, match="robot_?[rR]un_?[iI]d"):
        BuildEpisodesJobParams.model_validate(payload)


@pytest.mark.parametrize("field", ["robot_id", "robotId"])
def test_params_reject_caller_robot_identity(field: str) -> None:
    """A caller cannot relabel a registered recording as another robot's:
    a robot_id in the params fails validation (HTTP 400 at job creation)
    before any decoding or write -- it is neither honored nor ignored."""
    with pytest.raises(ValidationError, match=f"{field} is not accepted"):
        BuildEpisodesJobParams.model_validate(
            {
                "dataset_id": "d1",
                "dataset_version": "v1",
                "robot_run_id": "run-1",
                field: "robot-b",
            }
        )
