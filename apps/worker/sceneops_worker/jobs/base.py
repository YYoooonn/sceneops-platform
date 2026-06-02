from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from pydantic import BaseModel

from sceneops_core.common.schemas import ErrorInfo, JsonDict
from sceneops_core.jobs.contracts import JobExecutor
from sceneops_core.jobs.schemas import JobManifest, JobType
from sceneops_core.runs.schemas import BaseRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.jobs.context import JobContext

JobParamsT = TypeVar("JobParamsT", bound=BaseModel)
JobResultT = TypeVar("JobResultT", bound=BaseModel)
RunRecordT = TypeVar("RunRecordT", bound=BaseRunRecord)


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

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict: ...

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]: ...


AnyJobHandler: TypeAlias = JobHandler[Any, Any]


class RunRecordHandler(Generic[JobParamsT, JobResultT, RunRecordT]):
    """Base class that manages the RUNNING → SUCCEEDED / FAILED run-record lifecycle.

    Concrete handlers implement:
    - ``job_type``, ``params_model``: identity
    - ``build_initial_record``: returns a RUNNING-state record with all immutable fields set
    - ``execute``: performs the actual work, may mutate the run record, returns (record, result)
    - ``build_step_params`` / ``extract_context_updates``: pipeline planner / propagator hooks

    The base class owns:
    - initial upsert with RUNNING status
    - FAILED upsert with error on exception
    - started_at / finished_at timestamps
    """

    async def run(
        self,
        request: JobHandlerRequest[JobParamsT],
    ) -> JobResultT:
        job = request.job
        params = request.params
        context = request.context
        started_at = utc_now()

        initial_record = self.build_initial_record(
            job=job, params=params, started_at=started_at
        )
        await self._upsert(context, initial_record)

        try:
            succeeded_record, result = await self.execute(
                job=job,
                params=params,
                context=context,
                initial_record=initial_record,
                started_at=started_at,
            )
            await self._upsert(context, succeeded_record)
            return result

        except Exception as exc:
            failed_record = initial_record.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "error": ErrorInfo(
                        type=type(exc).__name__,
                        message=str(exc),
                    ),
                    "finished_at": utc_now(),
                }
            )
            await self._upsert(context, failed_record)
            raise

    def build_initial_record(
        self,
        *,
        job: JobManifest,
        params: JobParamsT,
        started_at: Any,
    ) -> RunRecordT:
        raise NotImplementedError

    async def execute(
        self,
        *,
        job: JobManifest,
        params: JobParamsT,
        context: JobContext,
        initial_record: RunRecordT,
        started_at: Any,
    ) -> tuple[RunRecordT, JobResultT]:
        raise NotImplementedError

    async def _upsert(self, context: JobContext, record: RunRecordT) -> None:
        raise NotImplementedError
