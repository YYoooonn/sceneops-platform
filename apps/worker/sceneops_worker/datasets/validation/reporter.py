from __future__ import annotations

from sceneops_core.schemas.datasets.validation import DatasetValidationReport


async def save_validation_report(
    *,
    artifact_store,
    report: DatasetValidationReport,
) -> str:
    return await artifact_store.save_json_artifact(
        artifact_type="dataset_validation_reports",
        artifact_id=report.validation_run_id,
        filename="validation_report.json",
        payload=report.model_dump(mode="json"),
    )
