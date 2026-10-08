"""Job lease recovery on real PostgreSQL: an expired claim is requeued and
dispatched once, a spent claim budget fails the Job (and reports it to its
pipeline), concurrent passes dispatch once, and a pipeline-owned Job whose worker
died runs again under a new claim and lets its run advance.

The dispatcher records messages instead of sending them; the Celery side of
worker loss is ``tests/infrastructure/test_job_lease_recovery.py``. The tests
commit uniquely-identified rows into the disposable database of
`make test-integration`. A pass acts on every expired lease in the database, so
assertions are about the test's own Jobs only.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)
from sceneops_core.jobs.schemas import JobEventType, JobManifest, JobStatus, JobType
from sceneops_core.pipelines.builtin import get_pipeline_definition
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineType,
)
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.postgres.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)
from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_execution.jobs.lease_recovery import (
    JOB_CLAIM_BUDGET,
    JOB_LEASE_EXPIRED_ERROR_TYPE,
    LeaseRecoveryOutcome,
    recover_expired_leases,
)
from sceneops_worker.jobs.registry import JobHandlerRegistry
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.pipelines.orchestrator import PipelineOrchestrator

_RUNNABLE = {JobStatus.PENDING, JobStatus.QUEUED}
# The budget passed while setting up always requeues, so its error is never written.
_UNUSED_ERROR = ErrorInfo(type="unused", message="")


@pytest_asyncio.fixture(autouse=True)
async def _fresh_database_connection():
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set -- run via `make test-integration` "
            "against a running `make local-up` stack."
        )
    reset_async_engine_cache()
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"Postgres not reachable at SCENEOPS_DATABASE_URL: {exc}")
    yield
    await dispose_async_engine()


class _Dispatcher:
    """Records the messages a pass sends."""

    def __init__(self, *, fail: bool = False) -> None:
        self.jobs: list[str] = []
        self.advances: list[str] = []
        self.fail = fail

    def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.jobs.append(job_id)
        return ExecutionDispatchResult(
            execution_id=f"exec-{uuid.uuid4().hex[:12]}",
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
        )

    def advance_pipeline(self, pipeline_run_id: str) -> None:
        self.advances.append(pipeline_run_id)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _expire(session, job_id: str) -> None:
    await session.execute(
        text(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
            "WHERE job_id = :job_id"
        ),
        {"job_id": job_id},
    )


async def _abandoned_claim(job: JobManifest, *, claims: int = 1) -> JobManifest:
    """``job`` committed, then claimed ``claims`` times by workers that all died:
    every claim but the last reclaimed, the last one RUNNING with a passed lease."""
    async with get_async_sessionmaker()() as session:
        repository = PostgresJobRepository(session)
        await repository.create(job)
        for claim in range(1, claims + 1):
            claimed = await repository.claim_for_run(
                job.job_id,
                worker_id=f"celery:dead-{claim}",
                runnable_statuses=_RUNNABLE,
                lease_seconds=60,
            )
            await _expire(session, job.job_id)
            if claim < claims:
                await repository.reclaim_expired_lease(
                    job.job_id, claim_budget=claims + 1, error=_UNUSED_ERROR
                )
        await session.commit()
        return claimed


def _job(**kw) -> JobManifest:
    return JobManifest(
        job_id=_id("job-lease"),
        type=JobType.PROFILE_SCENE,
        status=JobStatus.QUEUED,
        **kw,
    )


async def _stored(job_id: str) -> JobManifest:
    async with get_async_sessionmaker()() as session:
        return await PostgresJobRepository(session).get(job_id)


async def _pass(dispatcher: _Dispatcher):
    return await recover_expired_leases(
        session_factory=get_async_sessionmaker(),
        dispatcher=dispatcher,
        max_actions=10_000,
    )


def _mine(actions, job_id: str):
    return [action for action in actions if action.job_id == job_id]


async def test_an_expired_lease_is_requeued_and_dispatched_once():
    claimed = await _abandoned_claim(_job())
    dispatcher = _Dispatcher()

    (action,) = _mine(await _pass(dispatcher), claimed.job_id)
    again = _mine(await _pass(dispatcher), claimed.job_id)

    assert action.outcome == LeaseRecoveryOutcome.REQUEUED
    assert (action.expired_generation, action.expired_worker_id) == (1, "celery:dead-1")
    assert again == []  # QUEUED is not RUNNING: nothing left to recover
    assert dispatcher.jobs.count(claimed.job_id) == 1
    stored = await _stored(claimed.job_id)
    assert stored.status == JobStatus.QUEUED
    assert stored.retry_count == 0
    async with get_async_sessionmaker()() as session:
        events = await PostgresJobEventRepository(session).list_for_job(claimed.job_id)
        records = (
            await session.execute(
                text("SELECT count(*) FROM execution_records WHERE resource_id = :j"),
                {"j": claimed.job_id},
            )
        ).scalar_one()
    (requeued,) = [e for e in events if e.type == JobEventType.QUEUED]
    assert (requeued.attempt, requeued.worker_id) == (1, "celery:dead-1")
    assert records == 1


async def test_a_held_lease_is_left_alone():
    async with get_async_sessionmaker()() as session:
        repository = PostgresJobRepository(session)
        job = await repository.create(_job())
        await repository.claim_for_run(
            job.job_id,
            worker_id="celery:alive",
            runnable_statuses=_RUNNABLE,
            lease_seconds=60,
        )
        await session.commit()

    assert _mine(await _pass(_Dispatcher()), job.job_id) == []
    assert (await _stored(job.job_id)).status == JobStatus.RUNNING


async def test_a_spent_claim_budget_fails_the_job_and_reports_it_to_its_pipeline():
    claimed = await _abandoned_claim(
        _job(pipeline_run_id=_id("pipe")), claims=JOB_CLAIM_BUDGET
    )
    dispatcher = _Dispatcher()

    (action,) = _mine(await _pass(dispatcher), claimed.job_id)

    assert action.outcome == LeaseRecoveryOutcome.FAILED
    stored = await _stored(claimed.job_id)
    assert stored.status == JobStatus.FAILED
    assert stored.error.type == JOB_LEASE_EXPIRED_ERROR_TYPE
    assert claimed.job_id not in dispatcher.jobs
    assert dispatcher.advances == [claimed.pipeline_run_id]


async def test_concurrent_passes_dispatch_the_job_once():
    claimed = await _abandoned_claim(_job())
    dispatcher = _Dispatcher()

    passes = await asyncio.gather(*(_pass(dispatcher) for _ in range(6)))

    outcomes = [a.outcome for actions in passes for a in _mine(actions, claimed.job_id)]
    assert outcomes.count(LeaseRecoveryOutcome.REQUEUED) == 1
    assert set(outcomes) <= {
        LeaseRecoveryOutcome.REQUEUED,
        LeaseRecoveryOutcome.LOST_RACE,
    }
    assert dispatcher.jobs.count(claimed.job_id) == 1


async def test_a_failed_dispatch_leaves_the_job_queued():
    claimed = await _abandoned_claim(_job())

    (action,) = _mine(await _pass(_Dispatcher(fail=True)), claimed.job_id)

    assert action.outcome == LeaseRecoveryOutcome.DISPATCH_FAILED
    assert (await _stored(claimed.job_id)).status == JobStatus.QUEUED


# ── a pipeline-owned Job ──────────────────────────────────────────────────────


class _AnyParams(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Handler:
    """Stands in for the task's domain handler: the subject is ownership."""

    def __init__(self, job_type: JobType) -> None:
        self.job_type = job_type
        self.params_model = _AnyParams
        self.runs: list[str] = []

    async def run(self, request):
        self.runs.append(request.job.job_id)
        return {"ok": True}

    def build_job_params(self, inputs):
        return {}


class _Recorder:
    def __init__(self, context) -> None:
        self._context = context

    async def record(self, *, pipeline_run, task_definition, task_run, finished_job):
        task_run.status = PipelineTaskRunStatus.SUCCEEDED
        task_run.finished_at = utc_now()
        return await self._context.pipeline_store.save_task(task_run)


class _Gate:
    def check_task_result(self, *, task_definition, task_run) -> None:
        pass


class _Inputs:
    async def resolve(self, **_):
        return None


class _Planner:
    def build_job_for_task(self, *, pipeline_run, task, inputs) -> JobManifest:
        return JobManifest(
            job_id=_id("job-task"),
            type=task.job_type,
            status=JobStatus.QUEUED,
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_task_run_id=task.pipeline_task_run_id,
            pipeline_task_id=task.pipeline_task_id,
        )


async def _running_pipeline(job_type_of_first: JobType) -> tuple[str, str, list[str]]:
    """A RUNNING scene-building run whose first task waits on a Job (QUEUED)."""
    definition = get_pipeline_definition(PipelineType.RECORDING_SCENE_BUILDING)
    run_id, now = _id("pipe-lease"), utc_now()
    task_ids: list[str] = []
    async with get_async_sessionmaker()() as session:
        await PostgresPipelineRunRepository(session).create(
            PipelineRunManifest(
                pipeline_run_id=run_id,
                type=PipelineType.RECORDING_SCENE_BUILDING,
                status=PipelineRunStatus.RUNNING,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        tasks = sorted(definition.tasks, key=lambda t: t.order)
        first_job_id = _id("job-task")
        for task in tasks:
            task_run_id = _id("ptr")
            task_ids.append(task_run_id)
            first = task is tasks[0]
            await PostgresPipelineTaskRunRepository(session).create(
                PipelineTaskRunManifest(
                    pipeline_task_run_id=task_run_id,
                    pipeline_run_id=run_id,
                    pipeline_task_id=task.pipeline_task_id,
                    pipeline_task_name=task.name,
                    task_order=task.order,
                    status=(
                        PipelineTaskRunStatus.RUNNING
                        if first
                        else PipelineTaskRunStatus.PENDING
                    ),
                    job_type=JobType(task.job_type),
                    job_id=first_job_id if first else None,
                    started_at=now if first else None,
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()
    assert JobType(tasks[0].job_type) == job_type_of_first
    return run_id, first_job_id, task_ids


async def _advance(run_id: str, dispatcher: _Dispatcher) -> PipelineRunManifest:
    async with get_async_sessionmaker()() as session:
        context = create_worker_context(session, worker_id="celery:advance")
        return await PipelineOrchestrator(
            context,
            dispatcher=dispatcher,
            planner=_Planner(),
            quality_gate=_Gate(),
            input_resolver=_Inputs(),
            result_recorder=_Recorder(context),
        ).advance(run_id)


async def _task_runs(run_id: str) -> list[PipelineTaskRunManifest]:
    async with get_async_sessionmaker()() as session:
        tasks = await PostgresPipelineTaskRunRepository(session).list_for_pipeline_run(
            run_id
        )
    return sorted(tasks, key=lambda t: t.task_order)


async def test_a_pipeline_job_whose_worker_died_runs_again_and_its_run_advances():
    first_type = JobType.BUILD_RECORDING_SCENES
    run_id, job_id, task_run_ids = await _running_pipeline(first_type)
    dead = await _abandoned_claim(
        JobManifest(
            job_id=job_id,
            type=first_type,
            status=JobStatus.QUEUED,
            pipeline_run_id=run_id,
            pipeline_task_run_id=task_run_ids[0],
            pipeline_task_id="build_recording_scenes",
        )
    )
    dispatcher = _Dispatcher()

    # Before recovery: the run waits on a Job nothing will ever finish.
    assert (await _advance(run_id, dispatcher)).status == PipelineRunStatus.RUNNING
    assert dispatcher.jobs == []

    (action,) = _mine(await _pass(dispatcher), job_id)
    assert action.outcome == LeaseRecoveryOutcome.REQUEUED
    assert dispatcher.jobs == [job_id]
    # Still in flight for the run: requeued, not replaced.
    assert (await _advance(run_id, dispatcher)).status == PipelineRunStatus.RUNNING
    assert (await _task_runs(run_id))[0].job_id == job_id

    # The new message reaches a live worker: a new claim runs the same Job.
    handler = _Handler(first_type)
    async with get_async_sessionmaker()() as session:
        context = create_worker_context(session, worker_id="celery:second")
        finished = await JobRunner(
            context,
            dispatcher=dispatcher,
            handler_registry=JobHandlerRegistry([handler]),
        ).run(job_id)
    assert finished.status == JobStatus.SUCCEEDED
    assert finished.lease_generation == dead.lease_generation + 1
    assert handler.runs == [job_id]
    assert dispatcher.advances == [run_id]

    # The dead claim, had it survived, writes nothing.
    async with get_async_sessionmaker()() as session:
        late = await PostgresJobRepository(session).update_owned_run(
            dead.model_copy(update={"status": JobStatus.FAILED}),
            lease_generation=dead.lease_generation,
        )
        await session.commit()
    assert late is None
    assert (await _stored(job_id)).status == JobStatus.SUCCEEDED

    # The run's report: the task settles and the next one is submitted.
    assert (await _advance(run_id, dispatcher)).status == PipelineRunStatus.RUNNING
    tasks = await _task_runs(run_id)
    assert tasks[0].status == PipelineTaskRunStatus.SUCCEEDED
    assert tasks[1].status == PipelineTaskRunStatus.RUNNING
    assert dispatcher.jobs == [job_id, tasks[1].job_id]
