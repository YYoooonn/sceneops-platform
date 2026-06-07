from __future__ import annotations

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
from sceneops_core.runs.schemas import RunStatus
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

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        scene_manifest_uris = (
            base.get("scene_manifest_uris")
            or context_values.get("scene_manifest_uris")
            or []
        )
        return {**base, "scene_manifest_uris": scene_manifest_uris}

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
                dataset_id=job.params.get("dataset_id"),
                dataset_version=job.params.get("dataset_version"),
                run_id=run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
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
            observed_channels=observed_channels,
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
