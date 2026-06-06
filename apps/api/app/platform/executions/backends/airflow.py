from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.executions.schemas import ExecutionDispatchResult


@dataclass(frozen=True)
class AirflowPipelineExecutionBackend:
    base_url: str
    pipeline_dag_id: str
    username: str | None = None
    password: str | None = None

    async def dispatch_pipeline(self, pipeline_run_id: str) -> ExecutionDispatchResult:
        raise NotImplementedError(
            "Airflow pipeline execution backend is not yet implemented. "
            "Set pipeline_backend=celery to use the Celery backend."
        )
