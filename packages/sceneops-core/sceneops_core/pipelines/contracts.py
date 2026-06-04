from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable


PipelineExecutionRequestT = TypeVar(
    "PipelineExecutionRequestT",
    contravariant=True,
)
PipelineExecutionResultT = TypeVar(
    "PipelineExecutionResultT",
    covariant=True,
)

PipelineDispatchRequestT = TypeVar(
    "PipelineDispatchRequestT",
    contravariant=True,
)
PipelineDispatchResultT = TypeVar(
    "PipelineDispatchResultT",
    covariant=True,
)


@runtime_checkable
class PipelineExecutor(
    Protocol,
    Generic[PipelineExecutionRequestT, PipelineExecutionResultT],
):
    """Port-like contract for executing a SceneOps pipeline run.

    A PipelineExecutor runs an already-created pipeline run. It is responsible
    for resolving the pipeline definition, creating/dispatching step jobs,
    waiting for terminal step states, and producing a PipelineRunResult.
    """

    async def run(
        self,
        request: PipelineExecutionRequestT,
    ) -> PipelineExecutionResultT: ...


@runtime_checkable
class PipelineDispatcher(
    Protocol,
    Generic[PipelineDispatchRequestT, PipelineDispatchResultT],
):
    """Port-like contract for dispatching a pipeline run to an execution backend.

    A PipelineDispatcher does not execute the pipeline directly. It submits a
    pipeline run to an execution backend such as Celery, Airflow, local queue,
    or a remote orchestration service.
    """

    async def dispatch(
        self,
        request: PipelineDispatchRequestT,
    ) -> PipelineDispatchResultT: ...
