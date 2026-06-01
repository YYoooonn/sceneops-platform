from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import JobManifest, JobType
from sceneops_worker.runtime.context import JobContext

ParamsT = TypeVar("ParamsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class JobHandler(Protocol):
    job_type: JobType

    async def execute(self, job: JobManifest) -> JsonDict: ...


class TypedJobHandler(Generic[ParamsT, ResultT]):
    job_type: JobType

    def __init__(self, context: JobContext) -> None:
        self.context = context

    async def execute(self, job: JobManifest) -> JsonDict:
        params = self.parse_params(job)
        result = await self.run(params=params, job=job)
        return result.model_dump(mode="json")

    def parse_params(self, job: JobManifest) -> ParamsT:
        raise NotImplementedError

    async def run(self, *, params: ParamsT, job: JobManifest) -> ResultT:
        raise NotImplementedError
