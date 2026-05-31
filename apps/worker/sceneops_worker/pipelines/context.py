from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.pipelines import PipelineRunManifest, PipelineStepResult


@dataclass
class PipelineExecutionContext:
    pipeline_run_id: str
    dataset_id: str
    dataset_version: str
    model_id: str | None = None
    model_version: str | None = None
    values: JsonDict = field(default_factory=dict)
    steps: dict[str, JsonDict] = field(default_factory=dict)

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

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def mark_step(
        self,
        *,
        step_name: str,
        status: str,
        job_id: str | None,
        result: PipelineStepResult | None = None,
    ) -> None:
        self.steps[step_name] = {
            "status": status,
            "job_id": job_id,
            "result": result,
        }
