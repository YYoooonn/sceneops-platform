from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_core.common.schemas import JsonDict
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineTaskResult,
    PipelineTaskRunStatus,
)


@dataclass(frozen=True)
class PipelineTaskState:
    pipeline_task_id: str
    pipeline_task_name: str
    status: PipelineTaskRunStatus
    job_id: str | None = None
    result: PipelineTaskResult | None = None


@dataclass
class PipelineExecutionContext:
    pipeline_run_id: str
    dataset_id: str
    dataset_version: str
    model_id: str | None = None
    model_version: str | None = None
    values: JsonDict = field(default_factory=dict)
    tasks: dict[str, PipelineTaskState] = field(default_factory=dict)

    @classmethod
    def from_pipeline_run(
        cls,
        pipeline_run: PipelineRunManifest,
    ) -> "PipelineExecutionContext":
        return cls(
            pipeline_run_id=pipeline_run.pipeline_run_id,
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            model_id=pipeline_run.model_id,
            model_version=pipeline_run.model_version,
            values={
                "pipeline_run_id": pipeline_run.pipeline_run_id,
                "dataset_id": pipeline_run.dataset_id,
                "dataset_version": pipeline_run.dataset_version,
                "model_id": pipeline_run.model_id,
                "model_version": pipeline_run.model_version,
            },
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def require(self, key: str) -> Any:
        value = self.values.get(key)
        if value is None:
            raise ValueError(f"Pipeline context value is required: {key}")
        return value

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def mark_task(
        self,
        *,
        pipeline_task_id: str,
        pipeline_task_name: str,
        status: PipelineTaskRunStatus,
        job_id: str | None,
        result: PipelineTaskResult | None = None,
    ) -> None:
        self.tasks[pipeline_task_id] = PipelineTaskState(
            pipeline_task_id=pipeline_task_id,
            pipeline_task_name=pipeline_task_name,
            status=status,
            job_id=job_id,
            result=result,
        )

    def require_task_succeeded(self, pipeline_task_id: str) -> None:
        state = self.tasks.get(pipeline_task_id)

        if state is None:
            raise RuntimeError(
                f"Pipeline task dependency has not completed: {pipeline_task_id}"
            )

        if state.status != PipelineTaskRunStatus.SUCCEEDED:
            raise RuntimeError(
                f"Pipeline task dependency is not succeeded: "
                f"{pipeline_task_id} (status={state.status})"
            )
