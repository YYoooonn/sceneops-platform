from __future__ import annotations

from dataclasses import dataclass

import httpx

from sceneops_core.common.ids import generate_execution_id
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)
from sceneops_core.pipelines.schemas import PipelineType


@dataclass(frozen=True)
class AirflowPipelineExecutionBackend:
    base_url: str
    # Each pipeline type is its own DAG, named ``<prefix>_<pipeline type>``
    # (airflow/dags/sceneops_pipelines.py).
    pipeline_dag_prefix: str
    username: str | None = None
    password: str | None = None

    def dag_id(self, pipeline_type: PipelineType) -> str:
        return f"{self.pipeline_dag_prefix}_{pipeline_type.value}"

    async def dispatch_pipeline(
        self, pipeline_run_id: str, pipeline_type: PipelineType
    ) -> ExecutionDispatchResult:
        auth = (self.username, self.password) if self.username else None

        async with httpx.AsyncClient(
            base_url=self.base_url, auth=auth, timeout=10.0
        ) as client:
            response = await client.post(
                f"/api/v1/dags/{self.dag_id(pipeline_type)}/dagRuns",
                json={
                    "dag_run_id": pipeline_run_id,
                    "conf": {"pipeline_run_id": pipeline_run_id},
                },
            )
            response.raise_for_status()

        return ExecutionDispatchResult(
            execution_id=generate_execution_id(),
            external_id=pipeline_run_id,
            execution_backend=ExecutionBackend.AIRFLOW,
            execution_kind=ExecutionKind.PIPELINE_RUN,
            resource_id=pipeline_run_id,
            status=ExecutionStatus.QUEUED,
        )
