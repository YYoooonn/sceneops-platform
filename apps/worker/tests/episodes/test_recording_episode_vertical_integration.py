"""RobotRun -> BUILD_RECORDING_EPISODES -> REGISTER_EPISODES ->
VALIDATE_EPISODE / PROFILE_EPISODE on real PostgreSQL and real MinIO.

The recording is a real ROS 2 CDR MCAP stored in MinIO as a registered
RobotRun's recording artifact and read through ``resolve_recording``. Every
step runs in its own session, like separate jobs. Covers the pipeline
contract: retry convergence, conflict / replacement, a failure after a
partial payload write, concurrent registration, and Scene / Episode
independence over one RobotRun. Requires SCENEOPS_DATABASE_URL (migrated to
head) and a reachable MinIO; skips otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildRecordingEpisodesJobParams,
    BuildRecordingScenesJobParams,
    ProfileEpisodeJobParams,
    RegisterEpisodesJobParams,
    RegisterScenesJobParams,
    ValidateEpisodeJobParams,
)
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_episodes import recording_builder as episode_builder_module
from sceneops_worker.episodes.registration import EpisodeRegistrationConflictError
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset import build_recording_episodes as build_job_module
from sceneops_worker.jobs.dataset.build_recording_episodes import (
    BuildRecordingEpisodesJobHandler,
)
from sceneops_worker.jobs.dataset.build_recording_scenes import (
    BuildRecordingScenesJobHandler,
)
from sceneops_worker.jobs.dataset.profile_episode import ProfileEpisodeJobHandler
from sceneops_worker.jobs.dataset.register_episodes import RegisterEpisodesJobHandler
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler
from sceneops_worker.jobs.dataset.validate_episode import ValidateEpisodeJobHandler

sys.path.insert(0, str(Path(__file__).parent))
from sceneops_recording.testing.episode_recordings import (  # noqa: E402
    ODOM,
    episode_build_config,
    episode_recording,
    with_odometry,
)
from sceneops_recording.testing.ros2_recordings import (  # noqa: E402
    CAMERA_TOPIC,
    build_config as scene_build_config,
    default_recording,
)


def _job(job_id: str):
    job = MagicMock()
    job.job_id = job_id
    job.pipeline_run_id = None
    job.pipeline_task_run_id = None
    return job


class _Vertical:
    def __init__(self, settings, unique_id) -> None:
        self.settings = settings
        self.unique_id = unique_id
        self.dataset_id = unique_id("ds-recep")
        self.robot_id = unique_id("robot-recep")
        self.jobs = 0

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    async def run(self, handler, params):
        self.jobs += 1
        async with get_async_sessionmaker()() as session:
            result = await handler.run(
                JobHandlerRequest(
                    job=_job(f"job-{self.dataset_id}-{self.jobs}"),
                    params=params,
                    context=self.context(session),
                )
            )
            await session.commit()
            return result

    async def seed_dataset(self) -> None:
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            await ctx.dataset_store.create_dataset(
                DatasetRecord(dataset_id=self.dataset_id)
            )
            for version in ("v1", "v2"):
                await ctx.dataset_store.create_version(
                    DatasetVersionRecord(dataset_id=self.dataset_id, version=version)
                )
            await ctx.robot_store.create_robot_if_absent(
                RobotRecord(robot_id=self.robot_id)
            )
            await session.commit()

    async def register_run(self, recording: bytes) -> str:
        run_id = self.unique_id("run-recep")
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            uri = ctx.artifact_store.join_uri(
                self.settings.artifact.robot_run_root_uri, run_id, "recording.mcap"
            )
            await ctx.artifact_store.write_bytes(uri, recording)
            for artifact_id, kind, checksum, size, at in (
                (
                    robot_run_recording_artifact_id(run_id),
                    ArtifactKind.ROBOT_RUN_RECORDING,
                    "sha256:" + hashlib.sha256(recording).hexdigest(),
                    len(recording),
                    uri,
                ),
                (
                    robot_run_manifest_artifact_id(run_id),
                    ArtifactKind.ROBOT_RUN_MANIFEST,
                    "sha256:" + "0" * 64,
                    1,
                    f"{uri}.manifest.json",
                ),
            ):
                await ctx.artifact_record_store.create(
                    artifact_id=artifact_id,
                    ref=ArtifactRef(
                        kind=kind, uri=at, size_bytes=size, checksum=checksum
                    ),
                    owner_type=ArtifactOwnerType.ROBOT_RUN,
                    owner_id=run_id,
                )
            await ctx.robot_store.create_run(
                RobotRunRecord(
                    run_id=run_id,
                    robot_id=self.robot_id,
                    started_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
                    recording_format="mcap",
                    source_clock="mcap_log_time",
                    recording_artifact_id=robot_run_recording_artifact_id(run_id),
                    manifest_artifact_id=robot_run_manifest_artifact_id(run_id),
                    manifest_checksum="sha256:" + "0" * 64,
                )
            )
            await session.commit()
        return run_id

    def build(self, run_id, config=None, version="v1"):
        return BuildRecordingEpisodesJobParams(
            dataset_id=self.dataset_id,
            dataset_version=version,
            robot_run_id=run_id,
            build_config=config or episode_build_config(),
        )

    def register(self, built, replace=False, version="v1"):
        return RegisterEpisodesJobParams(
            dataset_id=self.dataset_id,
            dataset_version=version,
            manifest_artifact_ids=built.manifest_artifact_ids,
            replace=replace,
        )

    async def episodes(self, version="v1"):
        async with get_async_sessionmaker()() as session:
            return await self.context(session).episode_store.list(
                dataset_id=self.dataset_id, dataset_version=version
            )

    async def episode_count(self, version="v1") -> int:
        async with get_async_sessionmaker()() as session:
            dv = await self.context(session).dataset_store.get_version(
                dataset_id=self.dataset_id, version=version
            )
            return dv.episode.episode_count


@pytest.fixture()
async def vertical(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id
):
    dependencies_module._artifact_store = None
    env = _Vertical(worker_settings, unique_id)
    await env.seed_dataset()
    yield env
    dependencies_module._artifact_store = None


async def test_robot_run_to_registered_validated_episodes(vertical, tmp_path):
    run_id = await vertical.register_run(
        episode_recording().write(tmp_path / "r.mcap").read_bytes()
    )
    built = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_id)
    )
    assert built.unit_keys == ["task-mission-1-000"]
    assert (built.observation_count, built.state_count, built.action_count) == (3, 4, 3)
    assert built.payload_artifact_count == built.created_payload_count == 3

    registered = await vertical.run(
        RegisterEpisodesJobHandler(), vertical.register(built)
    )
    assert registered.created_episode_ids == registered.episode_ids
    assert await vertical.episode_count() == 1

    (episode,) = await vertical.episodes()
    assert episode.robot_run_id == run_id
    assert episode.manifest_artifact_id == built.manifest_artifact_ids[0]
    assert episode.window_clock == "vehicle.source_time"
    async with get_async_sessionmaker()() as session:
        ctx = vertical.context(session)
        manifest_record = await ctx.artifact_record_store.get(
            episode.manifest_artifact_id
        )
        manifest = await ctx.episode_artifact_store.read_pinned_manifest(
            uri=manifest_record.uri, checksum=episode.manifest_checksum
        )
        for observation in manifest.observations:
            record = await ctx.artifact_record_store.get(
                observation.payload.artifact_id
            )
            assert record.owner_id == run_id
            data = await ctx.artifact_store.read_bytes(record.uri)
            assert (
                "sha256:" + hashlib.sha256(data).hexdigest()
                == observation.payload.checksum
            )

    validated = await vertical.run(
        ValidateEpisodeJobHandler(),
        ValidateEpisodeJobParams(
            dataset_id=vertical.dataset_id,
            dataset_version="v1",
            episode_ids=registered.episode_ids,
        ),
    )
    profiled = await vertical.run(
        ProfileEpisodeJobHandler(),
        ProfileEpisodeJobParams(
            dataset_id=vertical.dataset_id,
            dataset_version="v1",
            episode_ids=registered.episode_ids,
        ),
    )
    assert validated.status == "ready"
    assert profiled.action_count == 3

    # Retry: same artifacts, nothing new, registration converges.
    again = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_id)
    )
    assert again.manifest_artifact_ids == built.manifest_artifact_ids
    assert again.created_payload_count == 0
    unchanged = await vertical.run(
        RegisterEpisodesJobHandler(), vertical.register(again)
    )
    assert unchanged.unchanged_episode_ids == registered.episode_ids

    # Changed build configuration: conflict, then explicit replacement.
    segmented = episode_build_config(
        segmentation={
            "policy": "fixed_duration",
            "clock": "vehicle.source_time",
            "duration_ns": 5_000_000_000,
        }
    )
    rebuilt = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_id, segmented)
    )
    assert rebuilt.created_payload_count == 0  # payload ids are config-independent
    with pytest.raises(EpisodeRegistrationConflictError):
        await vertical.run(RegisterEpisodesJobHandler(), vertical.register(rebuilt))
    assert [
        e.manifest_artifact_id for e in await vertical.episodes()
    ] == built.manifest_artifact_ids
    replaced = await vertical.run(
        RegisterEpisodesJobHandler(), vertical.register(rebuilt, replace=True)
    )
    assert len(replaced.episode_ids) == 2
    assert replaced.removed_episode_ids == registered.episode_ids
    assert await vertical.episode_count() == 2
    assert {e.producer_fingerprint for e in await vertical.episodes()} == {
        rebuilt.producer_fingerprint
    }


async def test_failure_after_partial_payload_write_converges_on_retry(
    vertical, tmp_path, monkeypatch
):
    run_id = await vertical.register_run(
        episode_recording().write(tmp_path / "r.mcap").read_bytes()
    )
    real = episode_builder_module.iter_planned_payloads

    def failing(path, plan, config):
        for index, item in enumerate(real(path, plan, config)):
            if index == 1:
                raise RuntimeError("worker crashed mid-build")
            yield item

    monkeypatch.setattr(build_job_module, "iter_planned_payloads", failing)
    with pytest.raises(RuntimeError, match="mid-build"):
        await vertical.run(BuildRecordingEpisodesJobHandler(), vertical.build(run_id))
    assert await vertical.episodes() == []

    monkeypatch.setattr(build_job_module, "iter_planned_payloads", real)
    built = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_id)
    )
    # The failed attempt's first payload bytes are reused, never duplicated.
    assert built.payload_artifact_count == 3
    await vertical.run(RegisterEpisodesJobHandler(), vertical.register(built))
    assert len(await vertical.episodes()) == 1


async def test_concurrent_identical_registrations_converge(vertical, tmp_path):
    run_id = await vertical.register_run(
        episode_recording().write(tmp_path / "r.mcap").read_bytes()
    )
    built = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_id)
    )
    results = await asyncio.gather(
        vertical.run(RegisterEpisodesJobHandler(), vertical.register(built)),
        vertical.run(RegisterEpisodesJobHandler(), vertical.register(built)),
    )
    created = sorted(len(r.created_episode_ids) for r in results)
    assert created == [0, 1]
    assert len(await vertical.episodes()) == 1
    assert await vertical.episode_count() == 1


async def test_scene_and_episode_building_are_independent_in_either_order(
    vertical, tmp_path
):
    """One RobotRun, two sibling canonical interpretations. Neither needs the
    other's records; the camera payloads they share are RobotRun-owned and
    extracted once."""
    rec = with_odometry(
        default_recording(), (1_000_000_000, 1_700_000_000, 2_400_000_000)
    )
    recording = rec.write(tmp_path / "both.mcap").read_bytes()
    header = {"source": "header_stamp", "clock": "sensor.header_stamp"}
    episode_config = {
        "streams": [
            {
                "topic": CAMERA_TOPIC,
                "role": "observation",
                "time": header,
                "payload": "compressed_image",
            },
            {
                "topic": ODOM,
                "role": "state",
                "time": header,
                "fields": [{"name": "x", "path": "pose.pose.position.x"}],
            },
        ],
        "segmentation": {"policy": "whole_recording", "clock": "sensor.header_stamp"},
    }

    def scene_params(run_id):
        return BuildRecordingScenesJobParams(
            dataset_id=vertical.dataset_id,
            dataset_version="v1",
            robot_run_id=run_id,
            build_config=scene_build_config(),
        )

    # Episodes first: no Scene exists for the run.
    run_a = await vertical.register_run(recording)
    episodes_a = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_a, episode_config)
    )
    await vertical.run(RegisterEpisodesJobHandler(), vertical.register(episodes_a))
    scenes_a = await vertical.run(BuildRecordingScenesJobHandler(), scene_params(run_a))
    await vertical.run(
        RegisterScenesJobHandler(),
        RegisterScenesJobParams(
            dataset_id=vertical.dataset_id,
            dataset_version="v1",
            manifest_artifact_ids=scenes_a.manifest_artifact_ids,
        ),
    )
    # The Scene build reused the four camera payloads; only lidar is new.
    assert episodes_a.created_payload_count == 4
    assert scenes_a.created_payload_count == scenes_a.payload_artifact_count - 4

    # Scenes first on another run: the Episode build creates no payload.
    run_b = await vertical.register_run(recording)
    scenes_b = await vertical.run(BuildRecordingScenesJobHandler(), scene_params(run_b))
    episodes_b = await vertical.run(
        BuildRecordingEpisodesJobHandler(), vertical.build(run_b, episode_config)
    )
    assert scenes_b.created_payload_count == scenes_b.payload_artifact_count
    assert episodes_b.created_payload_count == 0
    await vertical.run(RegisterEpisodesJobHandler(), vertical.register(episodes_b))

    episodes = await vertical.episodes()
    assert sorted(e.robot_run_id for e in episodes) == sorted([run_a, run_b])
    assert await vertical.episode_count() == 2
