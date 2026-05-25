from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.jobs import JobManifest, JobType
from sceneops_worker.jobs.context import JobExecutionContext


ParamsT = TypeVar("ParamsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class JobHandler(Protocol):
    job_type: JobType

    def execute(self, job: JobManifest) -> JsonDict: ...


class TypedJobHandler(Generic[ParamsT, ResultT]):
    job_type: JobType

    def __init__(self, context: JobExecutionContext) -> None:
        self.context = context

    def execute(self, job: JobManifest) -> JsonDict:
        params = self.parse_params(job)
        result = self.run(params=params, job=job)
        return result.model_dump(mode="json")

    def parse_params(self, job: JobManifest) -> ParamsT:
        raise NotImplementedError

    def run(self, *, params: ParamsT, job: JobManifest) -> ResultT:
        raise NotImplementedError
