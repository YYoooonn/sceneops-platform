from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.runs.schemas import RunType
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenarios.schemas.runs import (
    ScenarioMiningRunRecord,
    ScenarioReadinessRunRecord,
)

from sceneops_db.models.scenarios import ScenarioRunRecordModel, ScenarioSetModel

from ._utils import (
    base_run_to_values,
    error_from_json,
    metadata_from_model,
    values_with_metadata,
)


ScenarioRunRecord: TypeAlias = ScenarioMiningRunRecord | ScenarioReadinessRunRecord

_SCENARIO_RUN_TYPE_MAP: dict[str, type[ScenarioRunRecord]] = {
    RunType.SCENARIO_MINING.value: ScenarioMiningRunRecord,
    RunType.SCENARIO_READINESS.value: ScenarioReadinessRunRecord,
}


# ── ScenarioSet ───────────────────────────────────────────────────────────────


def scenario_set_model_to_record(model: ScenarioSetModel) -> ScenarioSetRecord:
    return ScenarioSetRecord(
        scenario_set_id=model.scenario_set_id,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        name=model.name,
        description=model.description,
        scenario_set_uri=model.scenario_set_uri,
        scenario_count=model.scenario_count,
        tags=model.tags or [],
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def scenario_set_record_to_values(record: ScenarioSetRecord) -> dict[str, Any]:
    return values_with_metadata(
        {
            "scenario_set_id": record.scenario_set_id,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "name": record.name,
            "description": record.description,
            "scenario_set_uri": record.scenario_set_uri,
            "scenario_count": record.scenario_count,
            "tags": record.tags,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "metadata": record.metadata,
        }
    )


# ── ScenarioRunRecord ─────────────────────────────────────────────────────────


def scenario_run_model_to_record(model: ScenarioRunRecordModel) -> ScenarioRunRecord:
    cls = _SCENARIO_RUN_TYPE_MAP.get(model.type)
    if cls is None:
        raise ValueError(f"Unknown scenario run type: {model.type!r}")

    base = dict(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_step_run_id=model.pipeline_step_run_id,
        job_id=model.job_id,
        params=model.params or {},
        result=model.result,
        error=error_from_json(model.error),
        artifact_root_uri=model.artifact_root_uri,
        manifest_uri=model.manifest_uri,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )

    if model.type == RunType.SCENARIO_MINING.value:
        return ScenarioMiningRunRecord(
            **base,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            dataset_manifest_uri=model.dataset_manifest_uri,
            scenario_set_id=model.scenario_set_id,
            scenario_set_uri=model.scenario_set_uri,
            mining_report_uri=model.report_uri,
            candidate_count=model.candidate_count,
            selected_count=model.selected_count,
            rejected_count=model.rejected_count,
            summary=model.summary or {},
        )
    else:  # SCENARIO_READINESS
        return ScenarioReadinessRunRecord(
            **base,
            scenario_set_id=model.scenario_set_id,
            scenario_set_uri=model.scenario_set_uri,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            readiness_report_uri=model.report_uri,
            ready_count=model.ready_count,
            blocked_count=model.blocked_count,
            warning_count=model.warning_count,
            average_score=model.average_score,
            summary=model.summary or {},
        )


def scenario_run_record_to_values(record: ScenarioRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)

    if isinstance(record, ScenarioMiningRunRecord):
        return {
            **base,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "dataset_manifest_uri": record.dataset_manifest_uri,
            "scenario_set_id": record.scenario_set_id,
            "scenario_set_uri": record.scenario_set_uri,
            "report_uri": record.mining_report_uri,
            "candidate_count": record.candidate_count,
            "selected_count": record.selected_count,
            "rejected_count": record.rejected_count,
            "summary": record.summary or {},
        }
    else:  # ScenarioReadinessRunRecord
        return {
            **base,
            "scenario_set_id": record.scenario_set_id,
            "scenario_set_uri": record.scenario_set_uri,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "report_uri": record.readiness_report_uri,
            "ready_count": record.ready_count,
            "blocked_count": record.blocked_count,
            "warning_count": record.warning_count,
            "average_score": record.average_score,
            "summary": record.summary or {},
        }
