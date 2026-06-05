from __future__ import annotations

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
from sceneops_core.runs.schemas import RunStatus
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

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        scene_manifest_uris = (
            base.get("scene_manifest_uris")
            or context_values.get("scene_manifest_uris")
            or []
        )
        return {**base, "scene_manifest_uris": scene_manifest_uris}

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = ValidateSceneJobResult.model_validate(result)
        return {
            "should_block_pipeline": parsed.should_block_pipeline,
            "validation_status": parsed.status,
            "validation_issue_count": parsed.issue_count,
            "checked_scene_count": parsed.checked_scene_count,
            "validation_report_uri": parsed.report_uri,
        }

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
            pipeline_step_run_id=job.pipeline_step_run_id,
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

        total_issues = 0
        blocking = False
        report_scenes: list[dict] = []

        for uri in uris:
            scene_manifest = await context.scene_artifact_store.load_scene_manifest(uri)
            if scene_manifest is None:
                total_issues += 1
                blocking = True
                report_scenes.append({"uri": uri, "error": "manifest_not_found"})
                continue

            result = _validator.validate(
                manifest=scene_manifest,
                required_channels=params.require_target_channels,
            )

            total_issues += len(result.issues)
            if result.should_block:
                blocking = True

            report_scenes.append(
                {
                    "scene_id": result.scene_id,
                    "uri": uri,
                    "status": result.status,
                    "sample_count": result.sample_count,
                    "observed_channels": result.observed_channels,
                    "missing_channels": result.missing_channels,
                    "issues": [i.model_dump() for i in result.issues],
                }
            )

        report = {
            "run_id": run_id,
            "job_id": job.job_id,
            "checked_scene_count": len(uris),
            "total_issues": total_issues,
            "should_block_pipeline": blocking,
            "status": "failed" if blocking else "ready",
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
                dataset_id=job.params.get("dataset_id"),
                dataset_version=job.params.get("dataset_version"),
                run_id=run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "validation_status": "failed" if blocking else "ready",
                "should_block_pipeline": blocking,
                "validation_report_uri": report_uri,
                "issue_count": total_issues,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, ValidateSceneJobResult(
            status="failed" if blocking else "ready",
            should_block_pipeline=blocking,
            checked_scene_count=len(uris),
            issue_count=total_issues,
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
