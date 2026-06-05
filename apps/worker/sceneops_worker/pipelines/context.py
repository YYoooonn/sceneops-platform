from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_core.common.schemas import JsonDict
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineStepResult,
    PipelineStepRunStatus,
)


@dataclass(frozen=True)
class PipelineStepState:
    pipeline_step_id: str
    pipeline_step_name: str
    status: PipelineStepRunStatus
    job_id: str | None = None
    result: PipelineStepResult | None = None


@dataclass
class PipelineExecutionContext:
    pipeline_run_id: str
    dataset_id: str
    dataset_version: str
    model_id: str | None = None
    model_version: str | None = None
    values: JsonDict = field(default_factory=dict)
    steps: dict[str, PipelineStepState] = field(default_factory=dict)

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

    def mark_step(
        self,
        *,
        pipeline_step_id: str,
        pipeline_step_name: str,
        status: PipelineStepRunStatus,
        job_id: str | None,
        result: PipelineStepResult | None = None,
    ) -> None:
        self.steps[pipeline_step_id] = PipelineStepState(
            pipeline_step_id=pipeline_step_id,
            pipeline_step_name=pipeline_step_name,
            status=status,
            job_id=job_id,
            result=result,
        )

    def require_step_succeeded(self, pipeline_step_id: str) -> None:
        state = self.steps.get(pipeline_step_id)

        if state is None:
            raise RuntimeError(
                f"Pipeline step dependency has not completed: {pipeline_step_id}"
            )

        if state.status != PipelineStepRunStatus.SUCCEEDED:
            raise RuntimeError(
                f"Pipeline step dependency is not succeeded: "
                f"{pipeline_step_id} (status={state.status})"
            )
