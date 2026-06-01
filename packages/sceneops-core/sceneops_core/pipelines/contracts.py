from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.common.types import JsonDict, Metadata, PipelineRunId


@runtime_checkable
class PipelineExecutor(Protocol):
    """Contract for executing a registered pipeline run."""

    def execute_pipeline(
        self,
        *,
        pipeline_run_id: PipelineRunId,
    ) -> JsonDict:
        ...


@runtime_checkable
class PipelineDispatcher(Protocol):
    """Contract for dispatching a pipeline run to an execution backend."""

    def dispatch_pipeline(
        self,
        *,
        pipeline_run_id: PipelineRunId,
        params: Metadata | None = None,
    ) -> PipelineRunId:
        ...
