from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_validation_run_id, generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobType,
    ValidateSceneJobParams,
    ValidateSceneJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.runs import SceneValidationRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.scenes.resolver import resolve_registered_scene
from sceneops_worker.scenes.validation import SceneManifestValidator

_validator = SceneManifestValidator()


class ValidateSceneJobHandler(
    RunRecordHandler[
        ValidateSceneJobParams, ValidateSceneJobResult, SceneValidationRunRecord
    ],
    JobHandler[ValidateSceneJobParams, ValidateSceneJobResult],
):
    """Validates registered Scenes at the revision each SceneRecord points to
    when the job reads it. Each per-scene run record pins that revision.

    Validation never changes membership or a SceneRecord, and caches nothing
    on the DatasetVersion: readiness is derived from these run records for
    the current revision (ADR-007 §13.4, §17.5)."""

    @property
    def job_type(self) -> JobType:
        return JobType.VALIDATE_SCENE

    @property
    def params_model(self) -> type[ValidateSceneJobParams]:
        return ValidateSceneJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        params: JsonDict = {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "scene_ids": inputs.refs.get("scene_ids") or [],
        }
        dataset_channels = inputs.dataset.required_channels if inputs.dataset else []
        if dataset_channels and not params.get("require_target_channels"):
            params["require_target_channels"] = dataset_channels
        return params

    def build_initial_record(
        self,
        *,
        job: Any,
        params: ValidateSceneJobParams,
        started_at: datetime,
    ) -> SceneValidationRunRecord:
        return SceneValidationRunRecord(
            run_id=default_validation_run_id(job.job_id),
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
        params: ValidateSceneJobParams,
        context: WorkerContext,
        initial_record: SceneValidationRunRecord,
        started_at: datetime,
    ) -> tuple[SceneValidationRunRecord, ValidateSceneJobResult]:
        run_id = initial_record.run_id

        if not params.scene_ids:
            failed_record = initial_record.model_copy(
                update={
                    "status": RunStatus.SUCCEEDED,
                    "validation_status": "failed",
                    "should_block_pipeline": True,
                    "issue_count": 1,
                    "error_count": 1,
                    "warning_count": 0,
                    "finished_at": utc_now(),
                }
            )
            return failed_record, ValidateSceneJobResult(
                status="failed",
                should_block_pipeline=True,
                checked_scene_count=0,
                issue_count=1,
                metadata={
                    "issues": [
                        {
                            "code": "empty_scene_input",
                            "message": "validate_scene requires at least one scene_id.",
                        }
                    ]
                },
            )

        total_issues = 0
        total_blocking = 0
        total_warnings = 0
        blocking = False
        report_scenes: list[dict] = []

        for scene_id in params.scene_ids:
            resolved = await resolve_registered_scene(context, scene_id)
            record = resolved.record
            result = _validator.validate(
                scene_id=scene_id,
                manifest=resolved.manifest,
                required_channels=params.require_target_channels,
                validate_keyframes=params.keyframe_validation.validate_keyframes,
                block_on_keyframe_missing_channels=(
                    params.keyframe_validation.block_on_keyframe_missing_channels
                ),
            )

            scene_errors = sum(1 for i in result.issues if i.blocking)
            scene_warnings = len(result.issues) - scene_errors
            total_issues += len(result.issues)
            total_blocking += scene_errors
            total_warnings += scene_warnings
            blocking = blocking or result.should_block

            report_scenes.append(
                {
                    "scene_id": scene_id,
                    "manifest_artifact_id": record.manifest_artifact_id,
                    "manifest_checksum": record.manifest_checksum,
                    "status": result.status,
                    "observation_count": result.observation_count,
                    "keyframe_count": result.keyframe_count,
                    "observed_channels": result.observed_channels,
                    "missing_channels": result.missing_channels,
                    "issues": [i.model_dump() for i in result.issues],
                }
            )

            per_scene_run_id = _per_scene_validation_run_id(job.job_id, scene_id)
            per_scene_report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_validations",
                per_scene_run_id,
                "report.json",
            )
            await context.artifact_store.write_json(
                per_scene_report_uri,
                {
                    "run_id": per_scene_run_id,
                    "job_id": job.job_id,
                    "scene_id": scene_id,
                    "should_block_pipeline": result.should_block,
                    "status": result.status,
                    "scenes": [report_scenes[-1]],
                    "created_at": utc_now().isoformat(),
                },
            )
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.DATASET_VALIDATION_REPORT,
                    uri=per_scene_report_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE_VALIDATION_RUN,
                owner_id=per_scene_run_id,
                scene_id=scene_id,
                dataset_id=record.dataset_id,
                dataset_version=record.dataset_version,
                run_id=per_scene_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )
            await context.runs.scene_runs.upsert(
                SceneValidationRunRecord(
                    run_id=per_scene_run_id,
                    scene_id=scene_id,
                    manifest_artifact_id=record.manifest_artifact_id,
                    manifest_checksum=record.manifest_checksum,
                    dataset_id=record.dataset_id,
                    dataset_version=record.dataset_version,
                    status=RunStatus.SUCCEEDED,
                    validation_status=result.status,
                    should_block_pipeline=result.should_block,
                    validation_report_uri=per_scene_report_uri,
                    issue_count=len(result.issues),
                    error_count=scene_errors,
                    warning_count=scene_warnings,
                    missing_channel_count=len(result.missing_channels),
                    checked_observation_count=result.observation_count,
                    checked_keyframe_count=result.keyframe_count,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_task_run_id=job.pipeline_task_run_id,
                    job_id=job.job_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )

        if blocking:
            overall_status = "failed"
        elif total_issues > 0:
            overall_status = "warning"
        else:
            overall_status = "ready"

        report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri, "scene_validations", run_id, "report.json"
        )
        await context.artifact_store.write_json(
            report_uri,
            {
                "run_id": run_id,
                "job_id": job.job_id,
                "checked_scene_count": len(params.scene_ids),
                "total_issues": total_issues,
                "should_block_pipeline": blocking,
                "status": overall_status,
                "scenes": report_scenes,
                "created_at": utc_now().isoformat(),
            },
        )
        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.DATASET_VALIDATION_REPORT,
                uri=report_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.SCENE_VALIDATION_RUN,
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
                "validation_status": overall_status,
                "should_block_pipeline": blocking,
                "validation_report_uri": report_uri,
                "issue_count": total_issues,
                "error_count": total_blocking,
                "warning_count": total_warnings,
                "finished_at": utc_now(),
            }
        )
        return succeeded_record, ValidateSceneJobResult(
            status=overall_status,
            should_block_pipeline=blocking,
            checked_scene_count=len(params.scene_ids),
            issue_count=total_issues,
            validation_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: SceneValidationRunRecord
    ) -> SceneValidationRunRecord:
        return await context.runs.scene_runs.upsert(record)


def _per_scene_validation_run_id(job_id: str, scene_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{scene_id}".encode()).hexdigest()[:12]
    return f"val-scene-{digest}"
