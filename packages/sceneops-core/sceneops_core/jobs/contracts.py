from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

JobExecutionRequestT = TypeVar("JobExecutionRequestT", contravariant=True)
JobExecutionResultT = TypeVar("JobExecutionResultT", covariant=True)

JobDispatchRequestT = TypeVar("JobDispatchRequestT", contravariant=True)
JobDispatchResultT = TypeVar("JobDispatchResultT", covariant=True)


@runtime_checkable
class JobExecutor(Protocol, Generic[JobExecutionRequestT, JobExecutionResultT]):
    """Port-like contract for executing a SceneOps job."""

    async def run(self, request: JobExecutionRequestT) -> JobExecutionResultT:
        ...


@runtime_checkable
class JobDispatcher(Protocol, Generic[JobDispatchRequestT, JobDispatchResultT]):
    """Port-like contract for dispatching a SceneOps job to an execution backend."""

    async def dispatch(self, request: JobDispatchRequestT) -> JobDispatchResultT:
        ...
