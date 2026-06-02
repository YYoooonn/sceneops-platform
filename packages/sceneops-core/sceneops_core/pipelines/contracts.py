from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

PipelineExecutionRequestT = TypeVar("PipelineExecutionRequestT", contravariant=True)
PipelineExecutionResultT = TypeVar("PipelineExecutionResultT", covariant=True)

PipelineDispatchRequestT = TypeVar("PipelineDispatchRequestT", contravariant=True)
PipelineDispatchResultT = TypeVar("PipelineDispatchResultT", covariant=True)


@runtime_checkable
class PipelineExecutor(
    Protocol,
    Generic[PipelineExecutionRequestT, PipelineExecutionResultT],
):
    """Port-like contract for executing a pipeline run."""

    async def run(
        self,
        request: PipelineExecutionRequestT,
    ) -> PipelineExecutionResultT: ...


@runtime_checkable
class PipelineDispatcher(
    Protocol,
    Generic[PipelineDispatchRequestT, PipelineDispatchResultT],
):
    """Port-like contract for dispatching a pipeline run to an execution backend."""

    async def dispatch(
        self,
        request: PipelineDispatchRequestT,
    ) -> PipelineDispatchResultT: ...
