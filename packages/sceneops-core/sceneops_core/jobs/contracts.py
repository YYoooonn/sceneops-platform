from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable


JobExecutionRequestT = TypeVar("JobExecutionRequestT", contravariant=True)
JobExecutionResultT = TypeVar("JobExecutionResultT", covariant=True)


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
