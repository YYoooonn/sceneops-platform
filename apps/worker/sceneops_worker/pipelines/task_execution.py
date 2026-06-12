from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sceneops_core.jobs.schemas import JobManifest
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineRunManifest,
    PipelineTaskDefinition,
    PipelineTaskInputs,
    PipelineTaskRunManifest,
)


class PipelineTaskOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    ALREADY_SUCCEEDED = "already_succeeded"
    ALREADY_SKIPPED = "already_skipped"


@dataclass
class PipelineTaskExecution:
    pipeline_run: PipelineRunManifest
    definition: PipelineDefinition
    task_definition: PipelineTaskDefinition
    task_run: PipelineTaskRunManifest
    inputs: PipelineTaskInputs | None = None
    job: JobManifest | None = None

    @property
    def pipeline_run_id(self) -> str:
        return self.pipeline_run.pipeline_run_id

    @property
    def task_id(self) -> str:
        return self.task_definition.pipeline_task_id

    def update_task_run(self, task_run: PipelineTaskRunManifest) -> None:
        self.task_run = task_run

    def update_inputs(self, inputs: PipelineTaskInputs) -> None:
        self.inputs = inputs

    def update_job(self, job: JobManifest) -> None:
        self.job = job

    def attach_job(
        self,
        *,
        job: JobManifest,
        task_run: PipelineTaskRunManifest,
    ) -> None:
        self.job = job
        self.task_run = task_run


@dataclass(frozen=True)
class PipelineTaskRunResult:
    pipeline_run: PipelineRunManifest
    task_definition: PipelineTaskDefinition
    task_run: PipelineTaskRunManifest
    outcome: PipelineTaskOutcome
    job: JobManifest | None = None
