from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.runs.schemas import BaseRunRecord


def dt_to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def enum_to_value(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    return v


def metadata_from_model(model: Any) -> dict[str, Any]:
    return getattr(model, "metadata_", None) or {}


def values_with_metadata(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    data["metadata_"] = data.pop("metadata", {}) or {}
    return data


def error_from_json(raw: dict[str, Any] | None) -> ErrorInfo | None:
    if raw is None:
        return None
    return ErrorInfo.model_validate(raw)


def error_to_json(error: ErrorInfo | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return error.model_dump(mode="json")


def base_run_to_values(record: BaseRunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "type": enum_to_value(record.type),
        "status": enum_to_value(record.status),
        "pipeline_run_id": record.pipeline_run_id,
        "pipeline_step_run_id": record.pipeline_step_run_id,
        "job_id": record.job_id,
        "params": record.params or {},
        "result": record.result,
        "error": error_to_json(record.error),
        "artifact_root_uri": record.artifact_root_uri,
        "manifest_uri": record.manifest_uri,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "metadata_": record.metadata or {},
    }
