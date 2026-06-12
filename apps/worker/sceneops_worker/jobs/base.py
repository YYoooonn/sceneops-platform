from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from pydantic import BaseModel

from sceneops_core.common.schemas import ErrorInfo, JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.contracts import JobExecutor
from sceneops_core.jobs.schemas import JobManifest, JobType
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import BaseRunRecord, RunStatus
from sceneops_worker.core.context import WorkerContext

JobParamsT = TypeVar("JobParamsT", bound=BaseModel)
JobResultT = TypeVar("JobResultT", bound=BaseModel)
RunRecordT = TypeVar("RunRecordT", bound=BaseRunRecord)


@dataclass(frozen=True)
class JobHandlerRequest(Generic[JobParamsT]):
    job: JobManifest
    params: JobParamsT
    context: WorkerContext


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

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict: ...


AnyJobHandler: TypeAlias = JobHandler[Any, Any]


class RunRecordHandler(Generic[JobParamsT, JobResultT, RunRecordT]):
    """Base class that manages the RUNNING → SUCCEEDED / FAILED run-record lifecycle.

    Concrete handlers implement:
    - ``job_type``, ``params_model``: identity
    - ``build_initial_record``: returns a RUNNING-state record with all immutable fields set
    - ``execute``: performs the actual work, may mutate the run record, returns (record, result)
    - ``build_job_params``: assembles job params from a typed PipelineTaskInputs envelope

    The base class owns:
    - initial upsert with RUNNING status
    - FAILED upsert with error on exception
    - started_at / finished_at timestamps

    Subclass contract:
    - ``execute`` should not call ``context.commit()`` or ``context.rollback()``
    - ``execute`` should raise exceptions instead of swallowing them
    - any artifact writes performed in ``execute`` may outlive a DB rollback
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

        saved_initial = await self._upsert(context, initial_record)
        await context.commit()

        try:
            succeeded_record, result = await self.execute(
                job=job,
                params=params,
                context=context,
                initial_record=saved_initial,
                started_at=started_at,
            )
            await self._upsert(context, succeeded_record)
            await context.commit()
            return result

        except Exception as exc:
            await context.rollback()
            failed_record = self.build_failed_record(
                job=job,
                params=params,
                context=context,
                saved_initial=saved_initial,
                exc=exc,
                started_at=started_at,
            )
            await self._upsert(context, failed_record)
            await context.commit()
            raise

    # pylint: disable=unused-argument
    def build_failed_record(
        self,
        *,
        job: JobManifest,
        params: JobParamsT,
        context: WorkerContext,
        saved_initial: RunRecordT,
        exc: Exception,
        started_at: datetime,
    ) -> RunRecordT:
        return saved_initial.model_copy(
            update={
                "status": RunStatus.FAILED,
                "error": ErrorInfo(
                    type=type(exc).__name__,
                    message=str(exc),
                ),
                "finished_at": utc_now(),
            }
        )

    def build_initial_record(
        self,
        *,
        job: JobManifest,
        params: JobParamsT,
        started_at: datetime,
    ) -> RunRecordT:
        raise NotImplementedError

    async def execute(
        self,
        *,
        job: JobManifest,
        params: JobParamsT,
        context: WorkerContext,
        initial_record: RunRecordT,
        started_at: datetime,
    ) -> tuple[RunRecordT, JobResultT]:
        raise NotImplementedError

    async def _upsert(self, context: WorkerContext, record: RunRecordT) -> RunRecordT:
        raise NotImplementedError
