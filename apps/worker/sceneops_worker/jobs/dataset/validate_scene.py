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
from sceneops_core.scenes.schemas.manifests import SceneManifest
from sceneops_core.scenes.schemas.runs import SceneValidationRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler


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
        report_data: list[dict] = []

        for uri in uris:
            scene_manifest = await context.dataset_artifact_store.load_scene_manifest(
                uri
            )
            if scene_manifest is None:
                total_issues += 1
                blocking = True
                report_data.append({"uri": uri, "error": "manifest_not_found"})
                continue

            issues = _validate_scene(
                manifest=scene_manifest,
                require_target_channels=params.require_target_channels,
            )

            total_issues += len(issues)
            if any(i.get("blocking") for i in issues):
                blocking = True

            report_data.append(
                {
                    "scene_id": scene_manifest.scene_id,
                    "uri": uri,
                    "issues": issues,
                    "sample_count": scene_manifest.sample_count,
                    "channels": scene_manifest.channels,
                }
            )

        report = {
            "run_id": run_id,
            "job_id": job.job_id,
            "checked_scene_count": len(uris),
            "total_issues": total_issues,
            "should_block_pipeline": blocking,
            "status": "failed" if blocking else "ready",
            "scenes": report_data,
            "created_at": utc_now().isoformat(),
        }

        report_uri: str | None = None
        if uris:
            report_uri = context.dataset_artifact_store.artifact_store.join_uri(
                context.settings.run_root_uri,
                "scene_validations",
                run_id,
                "report.json",
            )
            await context.dataset_artifact_store.artifact_store.write_json(
                report_uri, report
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


def _validate_scene(
    *,
    manifest: SceneManifest,
    require_target_channels: list[str],
) -> list[dict]:
    issues: list[dict] = []

    if manifest.sample_count == 0:
        issues.append(
            {
                "type": "empty_scene",
                "message": "Scene has no samples",
                "blocking": True,
            }
        )

    actual_channels = set(manifest.channels)
    for required in require_target_channels:
        if required not in actual_channels:
            issues.append(
                {
                    "type": "missing_channel",
                    "message": f"Required channel missing: {required}",
                    "channel": required,
                    "blocking": True,
                }
            )

    return issues
