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
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.runs import SceneProfileRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.scenes.profiling import SceneManifestProfiler

_profiler = SceneManifestProfiler()


class ProfileSceneJobHandler(
    RunRecordHandler[
        ProfileSceneJobParams, ProfileSceneJobResult, SceneProfileRunRecord
    ],
    JobHandler[ProfileSceneJobParams, ProfileSceneJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.PROFILE_SCENE

    @property
    def params_model(self) -> type[ProfileSceneJobParams]:
        return ProfileSceneJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        scene_manifest_uris = inputs.refs.get("scene_manifest_uris") or []
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "scene_manifest_uris": scene_manifest_uris,
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
            dataset_id=job.params.get("dataset_id"),
            dataset_version=job.params.get("dataset_version"),
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
        uris = _resolve_scene_manifest_uris(params)
        dataset_id = job.params.get("dataset_id")
        dataset_version = job.params.get("dataset_version")

        if not uris:
            raise ValueError("profile_scene requires at least one scene manifest URI.")

        total_samples = 0
        total_frames = 0
        total_annotations = 0
        all_channels: set[str] = set()
        scene_profiles: list[dict] = []

        for uri in uris:
            scene_manifest = await context.scene_artifact_store.load_scene_manifest(uri)
            if scene_manifest is None:
                continue

            result = _profiler.profile(manifest=scene_manifest)

            all_channels.update(result.channels)
            total_samples += result.sample_count
            total_frames += result.frame_count
            total_annotations += result.annotation_count

            scene_profiles.append(
                {
                    "scene_id": result.scene_id,
                    "sample_count": result.sample_count,
                    "frame_count": result.frame_count,
                    "channels": result.channels,
                    "annotation_count": result.annotation_count,
                }
            )

            # Store coverage metrics in asset_summary for later retrieval
            coverage_summary: JsonDict = {
                "calibration_coverage": result.calibration_coverage,
                "ego_pose_coverage": result.ego_pose_coverage,
                "camera_intrinsic_coverage": result.camera_intrinsic_coverage,
                "image_size_coverage": result.image_size_coverage,
                "category_distribution": result.category_distribution,
            }

            scene_id = result.scene_id
            per_scene_run_id = _per_scene_profile_run_id(job.job_id, scene_id)
            per_scene_report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_profiles",
                per_scene_run_id,
                "report.json",
            )
            per_scene_report = {
                "run_id": per_scene_run_id,
                "job_id": job.job_id,
                "scene_id": scene_id,
                "scene_count": 1,
                "sample_count": result.sample_count,
                "frame_count": result.frame_count,
                "annotation_count": result.annotation_count,
                "observed_channels": result.channels,
                "coverage": coverage_summary,
                "created_at": utc_now().isoformat(),
            }
            await context.artifact_store.write_json(
                per_scene_report_uri, per_scene_report
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
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=per_scene_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

            per_scene_record = SceneProfileRunRecord(
                run_id=per_scene_run_id,
                scene_id=scene_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=RunStatus.SUCCEEDED,
                sample_count=result.sample_count,
                frame_count=result.frame_count,
                annotation_count=result.annotation_count,
                observed_channels=result.channels,
                profile_report_uri=per_scene_report_uri,
                asset_summary=coverage_summary,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_task_run_id=job.pipeline_task_run_id,
                job_id=job.job_id,
                started_at=started_at,
                finished_at=utc_now(),
            )
            await context.runs.scene_runs.upsert(per_scene_record)

            # Update SceneRecord status to PROFILED
            scene_record = await context.scene_store.get(scene_id)
            if scene_record is not None:
                await context.scene_store.upsert(
                    scene_record.model_copy(update={"status": SceneStatus.PROFILED})
                )

        observed_channels = sorted(all_channels)

        report = {
            "run_id": run_id,
            "job_id": job.job_id,
            "scene_count": len(scene_profiles),
            "sample_count": total_samples,
            "frame_count": total_frames,
            "annotation_count": total_annotations,
            "observed_channels": observed_channels,
            "scenes": scene_profiles,
            "created_at": utc_now().isoformat(),
        }

        report_uri: str | None = None
        if uris:
            report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_profiles",
                run_id,
                "report.json",
            )
            await context.artifact_store.write_json(report_uri, report)

            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.DATASET_PROFILE_REPORT,
                    uri=report_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE_PROFILE_RUN,
                owner_id=run_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        if dataset_id and dataset_version:
            await context.dataset_store.update_quality_cache(
                dataset_id=dataset_id,
                version=dataset_version,
                latest_profile_run_id=run_id,
                profile_report_uri=report_uri,
            )

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "sample_count": total_samples,
                "frame_count": total_frames,
                "annotation_count": total_annotations,
                "observed_channels": observed_channels,
                "profile_report_uri": report_uri,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, ProfileSceneJobResult(
            scene_count=len(scene_profiles),
            sample_count=total_samples,
            frame_count=total_frames,
            annotation_count=total_annotations,
            observed_channels=observed_channels,
            profile_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: SceneProfileRunRecord
    ) -> SceneProfileRunRecord:
        return await context.runs.scene_runs.upsert(record)


def _resolve_scene_manifest_uris(params: ProfileSceneJobParams) -> list[str]:
    if params.scene_manifest_uris:
        return params.scene_manifest_uris
    if params.scene_manifest_uri:
        return [params.scene_manifest_uri]
    return []


def _per_scene_profile_run_id(job_id: str, scene_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{scene_id}".encode()).hexdigest()[:12]
    return f"profile-scene-{digest}"
