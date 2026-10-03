from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_profile_run_id, generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobType,
    ProfileSceneJobParams,
    ProfileSceneJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.runs import SceneProfileRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.scenes.profiling import SceneManifestProfiler
from sceneops_worker.scenes.resolver import resolve_registered_scene

_profiler = SceneManifestProfiler()


class ProfileSceneJobHandler(
    RunRecordHandler[
        ProfileSceneJobParams, ProfileSceneJobResult, SceneProfileRunRecord
    ],
    JobHandler[ProfileSceneJobParams, ProfileSceneJobResult],
):
    """Profiles registered Scenes at their current revision; each per-scene
    run record pins the revision it profiled. Never writes a SceneRecord or
    DatasetVersion state."""

    @property
    def job_type(self) -> JobType:
        return JobType.PROFILE_SCENE

    @property
    def params_model(self) -> type[ProfileSceneJobParams]:
        return ProfileSceneJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "scene_ids": inputs.refs.get("scene_ids") or [],
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: ProfileSceneJobParams,
        started_at: datetime,
    ) -> SceneProfileRunRecord:
        return SceneProfileRunRecord(
            run_id=default_profile_run_id(job.job_id),
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_task_run_id=job.pipeline_task_run_id,
            job_id=job.job_id,
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: ProfileSceneJobParams,
        context: WorkerContext,
        initial_record: SceneProfileRunRecord,
        started_at: datetime,
    ) -> tuple[SceneProfileRunRecord, ProfileSceneJobResult]:
        run_id = initial_record.run_id
        if not params.scene_ids:
            raise ValueError("profile_scene requires at least one scene_id.")

        total_observations = 0
        total_keyframes = 0
        total_annotations = 0
        all_channels: set[str] = set()
        scene_profiles: list[dict] = []

        for scene_id in params.scene_ids:
            resolved = await resolve_registered_scene(context, scene_id)
            record = resolved.record
            result = _profiler.profile(scene_id=scene_id, manifest=resolved.manifest)

            all_channels.update(result.observed_channels)
            total_observations += result.observation_count
            total_keyframes += result.keyframe_count
            total_annotations += result.annotation_count

            coverage: JsonDict = {
                "observations_by_channel": result.observations_by_channel,
                "calibration_coverage": result.calibration_coverage,
                "ego_pose_coverage": result.ego_pose_coverage,
                "camera_intrinsic_coverage": result.camera_intrinsic_coverage,
                "image_size_coverage": result.image_size_coverage,
            }
            scene_profile = {
                "scene_id": scene_id,
                "manifest_artifact_id": record.manifest_artifact_id,
                "manifest_checksum": record.manifest_checksum,
                "observation_count": result.observation_count,
                "keyframe_count": result.keyframe_count,
                "annotation_count": result.annotation_count,
                "observed_channels": result.observed_channels,
                "coverage": coverage,
                "category_distribution": result.category_distribution,
            }
            scene_profiles.append(scene_profile)

            per_scene_run_id = _per_scene_profile_run_id(job.job_id, scene_id)
            per_scene_report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_profiles",
                per_scene_run_id,
                "report.json",
            )
            await context.artifact_store.write_json(
                per_scene_report_uri,
                {
                    "run_id": per_scene_run_id,
                    "job_id": job.job_id,
                    **scene_profile,
                    "created_at": utc_now().isoformat(),
                },
            )
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.DATASET_PROFILE_REPORT,
                    uri=per_scene_report_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE_PROFILE_RUN,
                owner_id=per_scene_run_id,
                scene_id=scene_id,
                dataset_id=record.dataset_id,
                dataset_version=record.dataset_version,
                run_id=per_scene_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )
            await context.runs.scene_runs.upsert(
                SceneProfileRunRecord(
                    run_id=per_scene_run_id,
                    scene_id=scene_id,
                    manifest_artifact_id=record.manifest_artifact_id,
                    manifest_checksum=record.manifest_checksum,
                    dataset_id=record.dataset_id,
                    dataset_version=record.dataset_version,
                    status=RunStatus.SUCCEEDED,
                    observation_count=result.observation_count,
                    keyframe_count=result.keyframe_count,
                    annotation_count=result.annotation_count,
                    observed_channels=result.observed_channels,
                    profile_report_uri=per_scene_report_uri,
                    coverage=coverage,
                    annotation_summary={
                        "category_distribution": result.category_distribution
                    },
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_task_run_id=job.pipeline_task_run_id,
                    job_id=job.job_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )

        observed_channels = sorted(all_channels)
        report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri, "scene_profiles", run_id, "report.json"
        )
        await context.artifact_store.write_json(
            report_uri,
            {
                "run_id": run_id,
                "job_id": job.job_id,
                "scene_count": len(scene_profiles),
                "observation_count": total_observations,
                "keyframe_count": total_keyframes,
                "annotation_count": total_annotations,
                "observed_channels": observed_channels,
                "scenes": scene_profiles,
                "created_at": utc_now().isoformat(),
            },
        )
        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.DATASET_PROFILE_REPORT,
                uri=report_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.SCENE_PROFILE_RUN,
            owner_id=run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "observation_count": total_observations,
                "keyframe_count": total_keyframes,
                "annotation_count": total_annotations,
                "observed_channels": observed_channels,
                "profile_report_uri": report_uri,
                "finished_at": utc_now(),
            }
        )
        return succeeded_record, ProfileSceneJobResult(
            scene_count=len(scene_profiles),
            observation_count=total_observations,
            keyframe_count=total_keyframes,
            annotation_count=total_annotations,
            observed_channels=observed_channels,
            profile_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: SceneProfileRunRecord
    ) -> SceneProfileRunRecord:
        return await context.runs.scene_runs.upsert(record)


def _per_scene_profile_run_id(job_id: str, scene_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{scene_id}".encode()).hexdigest()[:12]
    return f"profile-scene-{digest}"
