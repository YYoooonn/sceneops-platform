from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)


@dataclass(frozen=True)
class AirflowExecutionDispatcher:
    base_url: str
    username: str | None
    password: str | None
    pipeline_dag_id: str
    job_dag_id: str

    def dispatch_pipeline_run(self, *, pipeline_run_id: str) -> ExecutionDispatchResult:
        dag_run_id = self._dag_run_id("pipeline", pipeline_run_id)
        self._trigger(
            self.pipeline_dag_id, dag_run_id, {"pipeline_run_id": pipeline_run_id}
        )
        return ExecutionDispatchResult(
            execution_id=dag_run_id,
            external_id=dag_run_id,
            execution_backend=ExecutionBackend.AIRFLOW,
            execution_kind=ExecutionKind.PIPELINE_RUN,
            resource_id=pipeline_run_id,
            status=ExecutionStatus.QUEUED,
        )

    def dispatch_job_run(self, *, job_id: str) -> ExecutionDispatchResult:
        dag_run_id = self._dag_run_id("job", job_id)
        self._trigger(self.job_dag_id, dag_run_id, {"job_id": job_id})
        return ExecutionDispatchResult(
            execution_id=dag_run_id,
            external_id=dag_run_id,
            execution_backend=ExecutionBackend.AIRFLOW,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
            status=ExecutionStatus.QUEUED,
        )

    def _trigger(self, dag_id: str, dag_run_id: str, conf: dict[str, str]) -> None:
        auth = (
            (self.username, self.password) if self.username and self.password else None
        )
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/v1/dags/{dag_id}/dagRuns",
            json={"dag_run_id": dag_run_id, "conf": conf},
            auth=auth,
            timeout=10,
        )
        response.raise_for_status()

    def _dag_run_id(self, prefix: str, resource_id: str) -> str:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"sceneops__{prefix}__{resource_id}__{ts}"
