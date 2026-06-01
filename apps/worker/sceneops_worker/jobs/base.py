from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from pydantic import BaseModel

from sceneops_core.jobs.contracts import JobExecutor
from sceneops_core.jobs.schemas import JobManifest, JobType
from sceneops_worker.jobs.context import JobContext

JobParamsT = TypeVar("JobParamsT", bound=BaseModel)
JobResultT = TypeVar("JobResultT", bound=BaseModel)


@dataclass(frozen=True)
class JobHandlerRequest(Generic[JobParamsT]):
    job: JobManifest
    params: JobParamsT
    context: JobContext


@runtime_checkable
class JobHandler(
    JobExecutor[JobHandlerRequest[JobParamsT], JobResultT],
    Protocol,
    Generic[JobParamsT, JobResultT],
):
    """Typed worker-side handler for one JobType."""

    @property
    def job_type(self) -> JobType: ...

    @property
    def params_model(self) -> type[JobParamsT]: ...

    async def run(self, request: JobHandlerRequest[JobParamsT]) -> JobResultT: ...


AnyJobHandler: TypeAlias = JobHandler[Any, Any]
