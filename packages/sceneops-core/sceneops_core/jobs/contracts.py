from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable


JobExecutionRequestT = TypeVar("JobExecutionRequestT", contravariant=True)
JobExecutionResultT = TypeVar("JobExecutionResultT", covariant=True)

JobDispatchRequestT = TypeVar("JobDispatchRequestT", contravariant=True)
JobDispatchResultT = TypeVar("JobDispatchResultT", covariant=True)


@runtime_checkable
class JobExecutor(
    Protocol,
    Generic[JobExecutionRequestT, JobExecutionResultT],
):
    """Port-like contract for executing a SceneOps job.

    A JobExecutor runs an already-created job and returns a job execution result.

    Examples:
    - local in-process executor
    - worker-side executor
    - job type router executor
    """

    async def run(
        self,
        request: JobExecutionRequestT,
    ) -> JobExecutionResultT: ...


@runtime_checkable
class JobDispatcher(
    Protocol,
    Generic[JobDispatchRequestT, JobDispatchResultT],
):
    """Port-like contract for dispatching a SceneOps job to an execution backend.

    A JobDispatcher submits a job to an execution backend and returns dispatch
    metadata. It does not execute the job directly.

    Examples:
    - Celery dispatcher
    - local queue dispatcher
    - Airflow DAG trigger dispatcher
    - remote worker dispatcher
    """

    async def dispatch(
        self,
        request: JobDispatchRequestT,
    ) -> JobDispatchResultT: ...
