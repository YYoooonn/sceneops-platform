"""RobotRun -> BUILD_RECORDING_SCENES -> REGISTER_SCENES -> VALIDATE_SCENE /
PROFILE_SCENE on real PostgreSQL and real MinIO.

The recording is a real ROS 2 CDR MCAP stored in MinIO as a registered
RobotRun's recording artifact, resolved through ``resolve_recording``.
Every step runs in its own session, like separate jobs. Requires
SCENEOPS_DATABASE_URL (migrated to head) and a reachable MinIO; skips
otherwise. Rows are created under unique ids and removed afterwards.
"""

from __future__ import annotations

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
    BuildRecordingScenesJobParams,
    ProfileSceneJobParams,
    RegisterScenesJobParams,
    ValidateSceneJobParams,
)
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_recording_scenes import (
    BuildRecordingScenesJobHandler,
)
from sceneops_worker.jobs.dataset.profile_scene import ProfileSceneJobHandler
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler
from sceneops_worker.jobs.dataset.validate_scene import ValidateSceneJobHandler
from sceneops_worker.scenes.registration import SceneRegistrationConflictError

sys.path.insert(0, str(Path(__file__).parent))
from recording_fixture import build_config, default_recording  # noqa: E402


def _job(job_id: str):
    job = MagicMock()
    job.job_id = job_id
    job.pipeline_run_id = None
    job.pipeline_task_run_id = None
    return job


class _Vertical:
    def __init__(self, settings, unique_id) -> None:
        self.settings = settings
        self.dataset_id = unique_id("ds-recscene")
        self.run_id = unique_id("run-recscene")
        self.robot_id = unique_id("robot-recscene")
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

    async def seed(self, recording: bytes) -> None:
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            await ctx.dataset_store.create_dataset(
                DatasetRecord(dataset_id=self.dataset_id)
            )
            await ctx.dataset_store.create_version(
                DatasetVersionRecord(dataset_id=self.dataset_id, version="v1")
            )
            await ctx.robot_store.create_robot_if_absent(
                RobotRecord(robot_id=self.robot_id)
            )
            uri = ctx.artifact_store.join_uri(
                self.settings.artifact.robot_run_root_uri, self.run_id, "recording.mcap"
            )
            await ctx.artifact_store.write_bytes(uri, recording)
            for artifact_id, kind, checksum, size, at in (
                (
                    robot_run_recording_artifact_id(self.run_id),
                    ArtifactKind.ROBOT_RUN_RECORDING,
                    "sha256:" + hashlib.sha256(recording).hexdigest(),
                    len(recording),
                    uri,
                ),
                (
                    robot_run_manifest_artifact_id(self.run_id),
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
                    owner_id=self.run_id,
                )
            await ctx.robot_store.create_run(
                RobotRunRecord(
                    run_id=self.run_id,
                    robot_id=self.robot_id,
                    started_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
                    recording_format="mcap",
                    source_clock="mcap_log_time",
                    recording_artifact_id=robot_run_recording_artifact_id(self.run_id),
                    manifest_artifact_id=robot_run_manifest_artifact_id(self.run_id),
                    manifest_checksum="sha256:" + "0" * 64,
                )
            )
            await session.commit()

    def build_params(self, config=None):
        return BuildRecordingScenesJobParams(
            dataset_id=self.dataset_id,
            dataset_version="v1",
            robot_run_id=self.run_id,
            build_config=config or build_config(),
        )

    def register_params(self, built, replace=False):
        return RegisterScenesJobParams(
            dataset_id=self.dataset_id,
            dataset_version="v1",
            manifest_artifact_ids=built.manifest_artifact_ids,
            replace=replace,
        )


@pytest.fixture()
async def vertical(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    env = _Vertical(worker_settings, unique_id)
    await env.seed(default_recording().write(tmp_path / "r.mcap").read_bytes())
    yield env
    dependencies_module._artifact_store = None


async def test_robot_run_to_registered_validated_scenes(vertical):
    built = await vertical.run(
        BuildRecordingScenesJobHandler(), vertical.build_params()
    )
    assert built.scene_count == 2 and built.payload_artifact_count == 8

    registered = await vertical.run(
        RegisterScenesJobHandler(), vertical.register_params(built)
    )
    assert registered.registered_scene_count == 2

    async with get_async_sessionmaker()() as session:
        ctx = vertical.context(session)
        scenes = await ctx.scene_store.list(
            dataset_id=vertical.dataset_id, dataset_version="v1"
        )
        assert {s.robot_run_id for s in scenes} == {vertical.run_id}
        assert {s.manifest_artifact_id for s in scenes} == set(
            built.manifest_artifact_ids
        )
        for scene in scenes:
            manifest_record = await ctx.artifact_record_store.get(
                scene.manifest_artifact_id
            )
            manifest = await ctx.scene_artifact_store.read_pinned_manifest(
                uri=manifest_record.uri, checksum=scene.manifest_checksum
            )
            for observation in manifest.observations:
                record = await ctx.artifact_record_store.get(
                    observation.payload.artifact_id
                )
                data = await ctx.artifact_store.read_bytes(record.uri)
                assert "sha256:" + hashlib.sha256(data).hexdigest() == (
                    observation.payload.checksum
                )
        version = await ctx.dataset_store.get_version(
            dataset_id=vertical.dataset_id, version="v1"
        )
        assert version.scene.scene_count == 2
        assert version.scene.observation_count == 8

    scene_ids = registered.scene_ids
    validated = await vertical.run(
        ValidateSceneJobHandler(),
        ValidateSceneJobParams(
            dataset_id=vertical.dataset_id, dataset_version="v1", scene_ids=scene_ids
        ),
    )
    profiled = await vertical.run(
        ProfileSceneJobHandler(),
        ProfileSceneJobParams(
            dataset_id=vertical.dataset_id, dataset_version="v1", scene_ids=scene_ids
        ),
    )
    assert validated.checked_scene_count == 2
    assert profiled.observation_count == 8

    # Retry: same artifacts, nothing new, registration converges.
    again = await vertical.run(
        BuildRecordingScenesJobHandler(), vertical.build_params()
    )
    assert again.manifest_artifact_ids == built.manifest_artifact_ids
    assert again.created_payload_count == 0
    unchanged = await vertical.run(
        RegisterScenesJobHandler(), vertical.register_params(again)
    )
    assert sorted(unchanged.unchanged_scene_ids) == sorted(scene_ids)

    # Changed build configuration: conflict, then explicit replacement.
    coarser = await vertical.run(
        BuildRecordingScenesJobHandler(),
        vertical.build_params(build_config(duration_ns=10_000_000_000)),
    )
    with pytest.raises(SceneRegistrationConflictError):
        await vertical.run(
            RegisterScenesJobHandler(), vertical.register_params(coarser)
        )
    replaced = await vertical.run(
        RegisterScenesJobHandler(), vertical.register_params(coarser, replace=True)
    )
    assert replaced.registered_scene_count == 1
    assert len(replaced.removed_scene_ids) == 1
