from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.runs import (
    DatasetDistributionRunRecord,
    DatasetExportRunRecord,
    DatasetProfileRunRecord,
    DatasetValidationRunRecord,
)
from sceneops_core.runs.schemas import RunType

from sceneops_db.models.datasets import (
    DatasetModel,
    DatasetRunRecordModel,
    DatasetVersionModel,
)

from ._utils import (
    base_run_to_values,
    enum_to_value,
    error_from_json,
    metadata_from_model,
    values_with_metadata,
)

DatasetRunRecord: TypeAlias = (
    DatasetValidationRunRecord
    | DatasetProfileRunRecord
    | DatasetDistributionRunRecord
    | DatasetExportRunRecord
)

_DATASET_RUN_TYPE_MAP: dict[str, type[DatasetRunRecord]] = {
    RunType.DATASET_VALIDATION.value: DatasetValidationRunRecord,
    RunType.DATASET_PROFILE.value: DatasetProfileRunRecord,
    RunType.DATASET_DISTRIBUTION.value: DatasetDistributionRunRecord,
    RunType.DATASET_EXPORT.value: DatasetExportRunRecord,
}


def make_dataset_version_id(dataset_id: str, version: str) -> str:
    return f"{dataset_id}:{version}"


# ── Dataset ──────────────────────────────────────────────────────────────────


def dataset_model_to_record(model: DatasetModel) -> DatasetRecord:
    return DatasetRecord(
        dataset_id=model.dataset_id,
        name=model.name,
        description=model.description,
        type=model.type,
        status=model.status,
        default_version=model.default_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def dataset_record_to_values(record: DatasetRecord) -> dict[str, Any]:
    return {
        "dataset_id": record.dataset_id,
        "name": record.name,
        "description": record.description,
        "type": enum_to_value(record.type),
        "status": enum_to_value(record.status),
        "default_version": record.default_version,
        "metadata_": record.metadata or {},
    }


# ── DatasetVersion ────────────────────────────────────────────────────────────


def dataset_version_model_to_record(
    model: DatasetVersionModel,
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        id=model.id,
        dataset_id=model.dataset_id,
        version=model.version,
        status=model.status,
        manifest_uri=model.manifest_uri,
        scene_count=model.scene_count,
        sample_count=model.sample_count,
        frame_count=model.frame_count,
        channels=model.channels or [],
        source_dataset_id=model.source_dataset_id,
        source_dataset_version=model.source_dataset_version,
        latest_validation_run_id=model.latest_validation_run_id,
        validation_status=model.validation_status,
        should_block_pipeline=model.should_block_pipeline,
        validation_report_uri=model.validation_report_uri,
        latest_profile_run_id=model.latest_profile_run_id,
        profile_report_uri=model.profile_report_uri,
        latest_distribution_run_id=model.latest_distribution_run_id,
        distribution_report_uri=model.distribution_report_uri,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def dataset_version_record_to_values(
    record: DatasetVersionRecord,
) -> dict[str, Any]:
    return values_with_metadata(
        {
            "id": record.id
            or make_dataset_version_id(
                record.dataset_id,
                record.version,
            ),
            "dataset_id": record.dataset_id,
            "version": record.version,
            "status": enum_to_value(record.status),
            "manifest_uri": record.manifest_uri,
            "scene_count": record.scene_count,
            "sample_count": record.sample_count,
            "frame_count": record.frame_count,
            "channels": record.channels,
            "source_dataset_id": record.source_dataset_id,
            "source_dataset_version": record.source_dataset_version,
            "latest_validation_run_id": record.latest_validation_run_id,
            "validation_status": enum_to_value(record.validation_status),
            "should_block_pipeline": record.should_block_pipeline,
            "validation_report_uri": record.validation_report_uri,
            "latest_profile_run_id": record.latest_profile_run_id,
            "profile_report_uri": record.profile_report_uri,
            "latest_distribution_run_id": record.latest_distribution_run_id,
            "distribution_report_uri": record.distribution_report_uri,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "metadata": record.metadata,
        }
    )


# ── DatasetRunRecord ──────────────────────────────────────────────────────────


def dataset_run_model_to_record(model: DatasetRunRecordModel) -> DatasetRunRecord:
    cls = _DATASET_RUN_TYPE_MAP.get(model.type)
    if cls is None:
        raise ValueError(f"Unknown dataset run type: {model.type!r}")

    base = dict(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
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

    if model.type == RunType.DATASET_VALIDATION.value:
        return DatasetValidationRunRecord(
            **base,
            dataset_manifest_uri=model.dataset_manifest_uri,
            validation_report_uri=model.report_uri,
            scope=model.scope or "full",
            summary=model.summary or {},
        )
    elif model.type == RunType.DATASET_PROFILE.value:
        return DatasetProfileRunRecord(
            **base,
            dataset_manifest_uri=model.dataset_manifest_uri,
            profile_report_uri=model.report_uri,
            scope=model.scope or "full",
        )
    elif model.type == RunType.DATASET_DISTRIBUTION.value:
        return DatasetDistributionRunRecord(
            **base,
            dataset_manifest_uri=model.dataset_manifest_uri,
            distribution_report_uri=model.report_uri,
            summary=model.summary or {},
        )
    else:  # DATASET_EXPORT
        return DatasetExportRunRecord(
            **base,
            dataset_manifest_uri=model.dataset_manifest_uri,
            output_format=model.output_format or "sceneops",
            export_uri=model.export_uri,
            summary=model.summary or {},
        )


def dataset_run_record_to_values(record: DatasetRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)
    common: dict[str, Any] = {
        **base,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "dataset_manifest_uri": record.dataset_manifest_uri,
    }

    if isinstance(record, DatasetValidationRunRecord):
        return {
            **common,
            "report_uri": record.validation_report_uri,
            "scope": enum_to_value(record.scope),
            "summary": record.summary or {},
        }
    elif isinstance(record, DatasetProfileRunRecord):
        return {
            **common,
            "report_uri": record.profile_report_uri,
            "scope": enum_to_value(record.scope),
        }
    elif isinstance(record, DatasetDistributionRunRecord):
        return {
            **common,
            "report_uri": record.distribution_report_uri,
            "summary": record.summary or {},
        }
    else:  # DatasetExportRunRecord
        return {
            **common,
            "export_uri": record.export_uri,
            "output_format": record.output_format,
            "summary": record.summary or {},
        }
