from __future__ import annotations

from typing import Any

from sceneops_core.executions.schemas import ExecutionDispatchResult

from sceneops_db.models.executions import ExecutionRecordModel

from ._utils import metadata_from_model


def execution_model_to_result(model: ExecutionRecordModel) -> ExecutionDispatchResult:
    return ExecutionDispatchResult(
        execution_id=model.execution_id,
        execution_backend=model.execution_backend,
        execution_kind=model.execution_kind,
        resource_id=model.resource_id,
        status=model.status,
        external_id=model.external_id,
        metadata=metadata_from_model(model),
    )


def execution_result_to_values(result: ExecutionDispatchResult) -> dict[str, Any]:
    return {
        "execution_id": result.execution_id,
        "execution_backend": result.execution_backend,
        "execution_kind": result.execution_kind,
        "resource_id": result.resource_id,
        "status": result.status,
        "external_id": result.external_id,
        "metadata_": result.metadata or {},
    }
