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
from sceneops_core.datasets.schemas import DatasetValidationStatus
from sceneops_core.jobs.schemas import (
    JobType,
    ValidateSceneJobParams,
    ValidateSceneJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.runs import SceneValidationRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.scenes.validation import SceneManifestValidator

_validator = SceneManifestValidator()


class ValidateSceneJobHandler(
    RunRecordHandler[
        ValidateSceneJobParams, ValidateSceneJobResult, SceneValidationRunRecord
    ],
    JobHandler[ValidateSceneJobParams, ValidateSceneJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.VALIDATE_SCENE

    @property
    def params_model(self) -> type[ValidateSceneJobParams]:
        return ValidateSceneJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        scene_manifest_uris = inputs.refs.get("scene_manifest_uris") or []
        params: JsonDict = {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "scene_manifest_uris": scene_manifest_uris,
        }
        # Inject dataset required_channels as require_target_channels unless already set.
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
        params: ValidateSceneJobParams,
        context: WorkerContext,
        initial_record: SceneValidationRunRecord,
        started_at: datetime,
    ) -> tuple[SceneValidationRunRecord, ValidateSceneJobResult]:
        run_id = initial_record.run_id
        uris = _resolve_scene_manifest_uris(params)
        dataset_id = job.params.get("dataset_id")
        dataset_version = job.params.get("dataset_version")

        if not uris:
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
                            "code": "empty_scene_manifest_input",
                            "message": "validate_scene requires at least one scene manifest URI.",
                        }
                    ]
                },
            )

        total_issues = 0
        total_blocking = 0
        total_warnings = 0
        blocking = False
        report_scenes: list[dict] = []

        for uri in uris:
            scene_manifest = await context.scene_artifact_store.load_scene_manifest(uri)
            if scene_manifest is None:
                total_issues += 1
                total_blocking += 1
                blocking = True
                report_scenes.append({"uri": uri, "error": "manifest_not_found"})
                continue

            result = _validator.validate(
                manifest=scene_manifest,
                required_channels=params.require_target_channels,
                validate_samples=params.sample_validation.validate_samples,
                block_on_sample_missing_channels=params.sample_validation.block_on_sample_missing_channels,
            )

            scene_error_count = sum(1 for i in result.issues if i.blocking)
            scene_warning_count = sum(1 for i in result.issues if not i.blocking)
            scene_issues = len(result.issues)

            total_issues += scene_issues
            total_blocking += scene_error_count
            total_warnings += scene_warning_count
            if result.should_block:
                blocking = True

            # Determine per-scene validation status
            if result.should_block:
                scene_val_status = "failed"
            elif scene_issues > 0:
                scene_val_status = "warning"
            else:
                scene_val_status = "ready"

            report_scenes.append(
                {
                    "scene_id": result.scene_id,
                    "uri": uri,
                    "status": scene_val_status,
                    "sample_count": result.sample_count,
                    "observed_channels": result.observed_channels,
                    "missing_channels": result.missing_channels,
                    "issues": [i.model_dump() for i in result.issues],
                }
            )

            # Upsert a per-scene SceneValidationRunRecord so the quality API can
            # look it up by scene_id.
            scene_id = result.scene_id
            per_scene_run_id = _per_scene_validation_run_id(job.job_id, scene_id)
            per_scene_report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_validations",
                per_scene_run_id,
                "report.json",
            )
            per_scene_report = {
                "run_id": per_scene_run_id,
                "job_id": job.job_id,
                "scene_id": scene_id,
                "checked_scene_count": 1,
                "total_issues": scene_issues,
                "should_block_pipeline": result.should_block,
                "status": scene_val_status,
                "scenes": [report_scenes[-1]],
                "created_at": utc_now().isoformat(),
            }
            await context.artifact_store.write_json(
                per_scene_report_uri, per_scene_report
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
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=per_scene_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

            per_scene_record = SceneValidationRunRecord(
                run_id=per_scene_run_id,
                scene_id=scene_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=RunStatus.SUCCEEDED,
                validation_status=scene_val_status,
                should_block_pipeline=result.should_block,
                validation_report_uri=per_scene_report_uri,
                issue_count=scene_issues,
                error_count=scene_error_count,
                warning_count=scene_warning_count,
                checked_sample_count=result.sample_count,
                checked_frame_count=result.frame_count,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_task_run_id=job.pipeline_task_run_id,
                job_id=job.job_id,
                started_at=started_at,
                finished_at=utc_now(),
            )
            await context.runs.scene_runs.upsert(per_scene_record)

            # Update SceneRecord status
            scene_record = await context.scene_store.get(scene_id)
            if scene_record is not None:
                new_status = (
                    SceneStatus.FAILED if result.should_block else SceneStatus.VALIDATED
                )
                await context.scene_store.upsert(
                    scene_record.model_copy(update={"status": new_status})
                )

        # Aggregate validation status for the overall job report
        if blocking:
            overall_status = "failed"
        elif total_issues > 0:
            overall_status = "warning"
        else:
            overall_status = "ready"

        report = {
            "run_id": run_id,
            "job_id": job.job_id,
            "checked_scene_count": len(uris),
            "total_issues": total_issues,
            "should_block_pipeline": blocking,
            "status": overall_status,
            "scenes": report_scenes,
            "created_at": utc_now().isoformat(),
        }

        report_uri: str | None = None
        if uris:
            report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_validations",
                run_id,
                "report.json",
            )
            await context.artifact_store.write_json(report_uri, report)

            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.DATASET_VALIDATION_REPORT,
                    uri=report_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE_VALIDATION_RUN,
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
                latest_validation_run_id=run_id,
                validation_status=DatasetValidationStatus(
                    "failed"
                    if blocking
                    else ("warning" if total_issues > 0 else "ready")
                ),
                should_block_pipeline=blocking,
                validation_report_uri=report_uri,
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
            checked_scene_count=len(uris),
            issue_count=total_issues,
            validation_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: SceneValidationRunRecord
    ) -> SceneValidationRunRecord:
        return await context.runs.scene_runs.upsert(record)


def _resolve_scene_manifest_uris(params: ValidateSceneJobParams) -> list[str]:
    if params.scene_manifest_uris:
        return params.scene_manifest_uris
    if params.scene_manifest_uri:
        return [params.scene_manifest_uri]
    return []


def _per_scene_validation_run_id(job_id: str, scene_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{scene_id}".encode()).hexdigest()[:12]
    return f"val-scene-{digest}"
