from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionKind,
    ExecutionStatus,
    ExecutionDispatchResult,
)
from app.modules.executions.dispatchers.base import ExecutionDispatcher


@dataclass(frozen=True)
class AirflowExecutionDispatcher(ExecutionDispatcher):
    base_url: str
    username: str | None
    password: str | None
    pipeline_dag_id: str
    job_dag_id: str

    def dispatch_pipeline_run(
        self,
        *,
        pipeline_run_id: str,
    ) -> ExecutionDispatchResult:
        dag_run_id = self._build_dag_run_id(
            prefix="pipeline",
            resource_id=pipeline_run_id,
        )

        self._trigger_dag_run(
            dag_id=self.pipeline_dag_id,
            dag_run_id=dag_run_id,
            conf={"pipeline_run_id": pipeline_run_id},
        )

        return ExecutionDispatchResult(
            execution_id=dag_run_id,
            external_id=dag_run_id,
            execution_backend=ExecutionBackend.AIRFLOW,
            execution_kind=ExecutionKind.PIPELINE_RUN,
            resource_id=pipeline_run_id,
            status=ExecutionStatus.QUEUED,
        )

    def dispatch_job_run(
        self,
        *,
        job_id: str,
    ) -> ExecutionDispatchResult:
        dag_run_id = self._build_dag_run_id(
            prefix="job",
            resource_id=job_id,
        )

        self._trigger_dag_run(
            dag_id=self.job_dag_id,
            dag_run_id=dag_run_id,
            conf={"job_id": job_id},
        )

        return ExecutionDispatchResult(
            execution_id=dag_run_id,
            external_id=dag_run_id,
            execution_backend=ExecutionBackend.AIRFLOW,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
            status=ExecutionStatus.QUEUED,
        )

    def _trigger_dag_run(
        self,
        *,
        dag_id: str,
        dag_run_id: str,
        conf: dict[str, str],
    ) -> None:
        auth = None
        if self.username and self.password:
            auth = (self.username, self.password)

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/v1/dags/{dag_id}/dagRuns",
            json={
                "dag_run_id": dag_run_id,
                "conf": conf,
            },
            auth=auth,
            timeout=10,
        )
        response.raise_for_status()

    def _build_dag_run_id(
        self,
        *,
        prefix: str,
        resource_id: str,
    ) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"sceneops__{prefix}__{resource_id}__{timestamp}"
