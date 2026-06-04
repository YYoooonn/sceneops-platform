from __future__ import annotations

from typing import Any

from sceneops_core.models.schemas.records import ModelRecord, ModelVersionRecord

from sceneops_db.models.model_registry import ModelModel, ModelVersionModel

from ._utils import enum_to_value, metadata_from_model


def make_model_version_id(model_id: str, version: str) -> str:
    return f"{model_id}:{version}"


# ── Model ─────────────────────────────────────────────────────────────────────


def model_model_to_record(model: ModelModel) -> ModelRecord:
    return ModelRecord(
        id=model.model_id,
        name=model.name,
        description=model.description,
        task_type=model.task_type,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def model_record_to_values(record: ModelRecord) -> dict[str, Any]:
    return {
        "model_id": record.id,
        "name": record.name,
        "description": record.description,
        "task_type": enum_to_value(record.task_type),
        "metadata_": record.metadata or {},
    }


# ── ModelVersion ──────────────────────────────────────────────────────────────


def model_version_model_to_record(model: ModelVersionModel) -> ModelVersionRecord:
    return ModelVersionRecord(
        id=model.id,
        model_id=model.model_id,
        version=model.version,
        task_type=model.task_type,
        backend=model.backend,
        status=model.status,
        model_uri=model.model_uri,
        endpoint_url=model.endpoint_url,
        artifact_manifest_uri=model.artifact_manifest_uri,
        runtime=model.runtime or {},
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def model_version_record_to_values(record: ModelVersionRecord) -> dict[str, Any]:
    meta = record.metadata or {}
    values: dict[str, Any] = {
        "id": record.id or make_model_version_id(record.model_id, record.version),
        "model_id": record.model_id,
        "version": record.version,
        "task_type": enum_to_value(record.task_type),
        "backend": enum_to_value(record.backend),
        "status": enum_to_value(record.status),
        "model_uri": record.model_uri,
        "endpoint_url": record.endpoint_url,
        "artifact_manifest_uri": record.artifact_manifest_uri,
        "runtime": record.runtime or {},
        "metadata_": meta,
    }
    return values
