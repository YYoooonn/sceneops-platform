"""Execution recovery on real PostgreSQL: a Job or PipelineRun whose message was
lost is sent again once it has waited ``resend_after``; a pass that dies before
or after its send leaves state that a later pass completes; concurrent passes
send each message once; and the duplicates this produces run a Job once and
advance a run once.

The dispatcher records messages instead of sending them (the Celery side is
``tests/infrastructure/test_lost_dispatch_recovery.py``). Waiting is simulated by
moving timestamps into the past (``_age``). A pass acts on every overdue row in
the database, so assertions are about the test's own rows only. The tests commit
uniquely-identified rows into the disposable database of `make test-integration`.
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
from sceneops_db.postgres import PostgresExecutionRecordRepository
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
from sceneops_worker.execution.recovery import (
    ResendOutcome,
    recover_execution,
    resend_overdue_advances,
    resend_overdue_dispatches,
)
from sceneops_worker.jobs.lease_recovery import LeaseRecoveryOutcome
from sceneops_worker.jobs.registry import JobHandlerRegistry
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.pipelines.orchestrator import PipelineOrchestrator

RESEND_AFTER = 300.0


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


class _Crash(BaseException):
    """Stands in for the recovery process dying at an exact point: not an
    ``Exception``, so nothing in the pass catches it."""


class _Dispatcher:
    """Records the messages a pass sends; fails (or dies at) the sends of the
    given ids, or of every id with ``fail=True``."""

    def __init__(
        self, *, fail: bool = False, crash_for: set[str] = frozenset()
    ) -> None:
        self.jobs: list[str] = []
        self.advances: list[str] = []
        self.fail = fail
        self.crash_for = crash_for

    def _send(self, resource_id: str) -> None:
        if resource_id in self.crash_for:
            raise _Crash("process died before the send")
        if self.fail:
            raise ConnectionError("broker unreachable")

    def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        self._send(job_id)
        self.jobs.append(job_id)
        return ExecutionDispatchResult(
            execution_id=f"exec-{uuid.uuid4().hex[:12]}",
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
        )

    def advance_pipeline(self, pipeline_run_id: str) -> None:
        self._send(pipeline_run_id)
        self.advances.append(pipeline_run_id)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _sql(statement: str, **params) -> None:
    async with get_async_sessionmaker()() as session:
        await session.execute(text(statement), params)
        await session.commit()


async def _age_job(job_id: str) -> None:
    """The Job has waited an hour since it was last dispatched or finished."""
    await _sql(
        "UPDATE jobs SET queued_at = now() - interval '1 hour', "
        "finished_at = finished_at - interval '1 hour', "
        "updated_at = now() - interval '1 hour' WHERE job_id = :j",
        j=job_id,
    )


async def _age_run(run_id: str) -> None:
    await _sql(
        "UPDATE pipeline_runs SET updated_at = now() - interval '1 hour' "
        "WHERE pipeline_run_id = :r",
        r=run_id,
    )


async def _queued_job(**kw) -> str:
    """A Job committed QUEUED whose message never arrived, an hour ago."""
    job_id = _id("job-resend")
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(
                job_id=job_id,
                type=JobType.PROFILE_SCENE,
                status=JobStatus.QUEUED,
                queued_at=utc_now(),
                **kw,
            )
        )
        await session.commit()
    await _age_job(job_id)
    return job_id


async def _job(job_id: str) -> JobManifest:
    async with get_async_sessionmaker()() as session:
        return await PostgresJobRepository(session).get(job_id)


async def _records(resource_id: str) -> int:
    async with get_async_sessionmaker()() as session:
        return (
            await session.execute(
                text("SELECT count(*) FROM execution_records WHERE resource_id = :r"),
                {"r": resource_id},
            )
        ).scalar_one()


async def _dispatches(dispatcher, **kw):
    return await resend_overdue_dispatches(
        session_factory=get_async_sessionmaker(),
        dispatcher=dispatcher,
        resend_after_seconds=RESEND_AFTER,
        max_actions=10_000,
        **kw,
    )


async def _advances(dispatcher):
    return await resend_overdue_advances(
        session_factory=get_async_sessionmaker(),
        dispatcher=dispatcher,
        resend_after_seconds=RESEND_AFTER,
        max_actions=10_000,
    )


def _mine(actions, resource_id: str) -> list[ResendOutcome]:
    return [a.outcome for a in actions if a.resource_id == resource_id]


class _AnyParams(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Handler:
    """Stands in for every domain handler: the subject is delivery."""

    def __init__(self, job_type: JobType) -> None:
        self.job_type = job_type
        self.params_model = _AnyParams
        self.runs: list[str] = []

    async def run(self, request):
        self.runs.append(request.job.job_id)
        return {"ok": True}

    def build_job_params(self, inputs):
        return {}


async def _run_job(job_id: str, handler: _Handler, dispatcher) -> JobManifest:
    async with get_async_sessionmaker()() as session:
        context = create_worker_context(session, worker_id=_id("celery"))
        return await JobRunner(
            context,
            dispatcher=dispatcher,
            handler_registry=JobHandlerRegistry([handler]),
        ).run(job_id)


# ── run_job ───────────────────────────────────────────────────────────────────


async def test_a_job_whose_message_was_lost_is_sent_again_once_per_interval():
    job_id = await _queued_job()
    dispatcher = _Dispatcher()

    first = _mine(await _dispatches(dispatcher), job_id)
    second = _mine(await _dispatches(dispatcher), job_id)  # within the interval

    assert first == [ResendOutcome.RESENT]
    assert second == []
    assert dispatcher.jobs.count(job_id) == 1
    assert await _records(job_id) == 1
    stored = await _job(job_id)
    assert stored.status == JobStatus.QUEUED  # a message, not a state change
    async with get_async_sessionmaker()() as session:
        events = await PostgresJobEventRepository(session).list_for_job(job_id)
    assert [e.type for e in events] == [JobEventType.QUEUED]


async def test_a_job_that_is_not_overdue_is_left_alone():
    job_id = _id("job-fresh")
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(
                job_id=job_id,
                type=JobType.PROFILE_SCENE,
                status=JobStatus.QUEUED,
                queued_at=utc_now(),
            )
        )
        await session.commit()

    assert _mine(await _dispatches(_Dispatcher()), job_id) == []


async def test_a_failed_send_is_retried_after_the_interval():
    job_id = await _queued_job()

    failed = _mine(await _dispatches(_Dispatcher(fail=True)), job_id)
    await _age_job(job_id)
    dispatcher = _Dispatcher()
    retried = _mine(await _dispatches(dispatcher), job_id)

    assert failed == [ResendOutcome.SEND_FAILED]
    assert retried == [ResendOutcome.RESENT]
    assert dispatcher.jobs.count(job_id) == 1
    assert await _records(job_id) == 1


async def test_a_pass_that_dies_before_its_send_is_completed_by_a_later_pass():
    job_id = await _queued_job()

    with pytest.raises(_Crash):
        await _dispatches(_Dispatcher(crash_for={job_id}))
    stored = await _job(job_id)
    assert stored.status == JobStatus.QUEUED  # the claim committed, nothing sent
    assert await _records(job_id) == 0
    assert _mine(await _dispatches(_Dispatcher()), job_id) == []  # not due yet

    await _age_job(job_id)
    dispatcher = _Dispatcher()
    assert _mine(await _dispatches(dispatcher), job_id) == [ResendOutcome.RESENT]
    assert dispatcher.jobs.count(job_id) == 1


async def test_a_pass_that_dies_after_its_send_causes_one_harmless_duplicate(
    monkeypatch,
):
    job_id = await _queued_job()
    dispatcher = _Dispatcher()
    real_create = PostgresExecutionRecordRepository.create

    async def die_after_send(self, execution):
        if execution.resource_id != job_id:
            return await real_create(self, execution)
        raise _Crash("process died after the send, before its record")

    monkeypatch.setattr(PostgresExecutionRecordRepository, "create", die_after_send)
    with pytest.raises(_Crash):
        await _dispatches(dispatcher)
    monkeypatch.setattr(PostgresExecutionRecordRepository, "create", real_create)
    await _age_job(job_id)  # the first message still waits in a long queue
    await _dispatches(dispatcher)

    assert dispatcher.jobs.count(job_id) == 2
    # Both messages are delivered: one claim runs the Job, the other is refused.
    handler = _Handler(JobType.PROFILE_SCENE)
    finished = await _run_job(job_id, handler, _Dispatcher())
    with pytest.raises(RuntimeError, match="already succeeded"):
        await _run_job(job_id, handler, _Dispatcher())
    assert finished.status == JobStatus.SUCCEEDED
    assert handler.runs == [job_id]


async def test_concurrent_passes_send_each_message_once():
    job_ids = [await _queued_job() for _ in range(10)]
    dispatcher = _Dispatcher()

    passes = await asyncio.gather(*(_dispatches(dispatcher) for _ in range(4)))

    for job_id in job_ids:
        outcomes = [o for actions in passes for o in _mine(actions, job_id)]
        assert outcomes.count(ResendOutcome.RESENT) == 1, outcomes
        assert set(outcomes) <= {ResendOutcome.RESENT, ResendOutcome.LOST_RACE}
        assert dispatcher.jobs.count(job_id) == 1


async def test_a_job_reclaimed_by_lease_recovery_whose_send_failed_is_sent_later():
    job_id = _id("job-lease")
    async with get_async_sessionmaker()() as session:
        repository = PostgresJobRepository(session)
        await repository.create(
            JobManifest(
                job_id=job_id, type=JobType.PROFILE_SCENE, status=JobStatus.QUEUED
            )
        )
        await repository.claim_for_run(
            job_id,
            worker_id="celery:dead",
            runnable_statuses={JobStatus.QUEUED},
            lease_seconds=60,
        )
        await session.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE job_id = :j"
            ),
            {"j": job_id},
        )
        await session.commit()

    lost = await recover_execution(
        session_factory=get_async_sessionmaker(),
        dispatcher=_Dispatcher(fail=True),
        resend_after_seconds=RESEND_AFTER,
        max_actions=10_000,
    )
    await _age_job(job_id)
    dispatcher = _Dispatcher()
    later = await recover_execution(
        session_factory=get_async_sessionmaker(),
        dispatcher=dispatcher,
        resend_after_seconds=RESEND_AFTER,
        max_actions=10_000,
    )

    assert [a.outcome for a in lost.leases if a.job_id == job_id] == [
        LeaseRecoveryOutcome.DISPATCH_FAILED
    ]
    assert [a.job_id for a in later.leases if a.job_id == job_id] == []
    assert _mine(later.dispatches, job_id) == [ResendOutcome.RESENT]
    assert dispatcher.jobs.count(job_id) == 1


# ── advance ───────────────────────────────────────────────────────────────────


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
            queued_at=utc_now(),
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_task_run_id=task.pipeline_task_run_id,
            pipeline_task_id=task.pipeline_task_id,
        )


_TYPE = PipelineType.EPISODE_LEARNING_DATA_BUILDING


async def _queued_run() -> str:
    """A PipelineRun committed QUEUED whose first ``advance`` never arrived."""
    definition = get_pipeline_definition(_TYPE)
    run_id, now = _id("pipe-resend"), utc_now()
    async with get_async_sessionmaker()() as session:
        await PostgresPipelineRunRepository(session).create(
            PipelineRunManifest(
                pipeline_run_id=run_id,
                type=_TYPE,
                status=PipelineRunStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
        )
        for task in definition.tasks:
            await PostgresPipelineTaskRunRepository(session).create(
                PipelineTaskRunManifest(
                    pipeline_task_run_id=_id("ptr"),
                    pipeline_run_id=run_id,
                    pipeline_task_id=task.pipeline_task_id,
                    pipeline_task_name=task.name,
                    task_order=task.order,
                    status=PipelineTaskRunStatus.PENDING,
                    job_type=JobType(task.job_type),
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()
    await _age_run(run_id)
    return run_id


async def _advance(run_id: str, dispatcher) -> PipelineRunManifest:
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


async def _tasks(run_id: str) -> list[PipelineTaskRunManifest]:
    async with get_async_sessionmaker()() as session:
        tasks = await PostgresPipelineTaskRunRepository(session).list_for_pipeline_run(
            run_id
        )
    return sorted(tasks, key=lambda t: t.task_order)


async def _run_status(run_id: str) -> PipelineRunStatus:
    async with get_async_sessionmaker()() as session:
        return (await PostgresPipelineRunRepository(session).get(run_id)).status


async def test_every_lost_message_of_a_pipeline_is_recovered_until_it_succeeds():
    """The start advance, each task Job's dispatch and each Job's report are
    lost once; recovery alone resends each and the run completes, with one Job
    per task and every message sent exactly once more."""
    run_id = await _queued_run()
    lost = _Dispatcher(fail=True)  # every send the platform makes is lost
    recovery = _Dispatcher()
    handlers = {
        t: _Handler(t) for t in (JobType.ALIGN_EPISODE, JobType.EXPORT_LEARNING_DATA)
    }

    # The start step never arrived.
    assert _mine(await _advances(recovery), run_id) == [ResendOutcome.RESENT]
    for task_index in range(2):
        # The resent advance runs; it submits the task's Job, whose dispatch is lost.
        with pytest.raises(ConnectionError):
            await _advance(run_id, lost)
        job_id = (await _tasks(run_id))[task_index].job_id
        assert _mine(await _advances(recovery), run_id) == []  # Job in flight
        await _age_job(job_id)
        assert _mine(await _dispatches(recovery), job_id) == [ResendOutcome.RESENT]
        # The resent job message runs it; its report is lost.
        job = await _job(job_id)
        with pytest.raises(ConnectionError):
            await _run_job(job_id, handlers[job.type], lost)
        assert (await _job(job_id)).status == JobStatus.SUCCEEDED
        await _age_job(job_id)
        await _age_run(run_id)
        assert _mine(await _advances(recovery), run_id) == [ResendOutcome.RESENT]

    final = await _advance(run_id, recovery)

    assert final.status == PipelineRunStatus.SUCCEEDED
    assert [t.status for t in await _tasks(run_id)] == [
        PipelineTaskRunStatus.SUCCEEDED,
        PipelineTaskRunStatus.SUCCEEDED,
    ]
    task_jobs = [t.job_id for t in await _tasks(run_id)]
    assert recovery.advances.count(run_id) == 3
    assert [j for j in recovery.jobs if j in task_jobs] == task_jobs
    assert [h.runs for h in handlers.values()] == [[j] for j in task_jobs]


async def test_duplicate_advances_submit_each_task_once():
    run_id = await _queued_run()
    dispatcher = _Dispatcher()

    await asyncio.gather(*(_advance(run_id, dispatcher) for _ in range(5)))

    tasks = await _tasks(run_id)
    assert await _run_status(run_id) == PipelineRunStatus.RUNNING
    assert tasks[0].status == PipelineTaskRunStatus.RUNNING
    assert dispatcher.jobs == [tasks[0].job_id]  # one Job submitted, once


async def test_a_failed_pipeline_job_whose_report_was_lost_ends_its_run():
    """Lease recovery fails a Job past its claim budget and reports it; when that
    report is lost, the advance sweep delivers it."""
    run_id = await _queued_run()
    await _advance(run_id, _Dispatcher())
    job_id = (await _tasks(run_id))[0].job_id
    await _sql(
        "UPDATE jobs SET status = 'failed', finished_at = now(), "
        "error = CAST(:e AS jsonb) WHERE job_id = :j",
        j=job_id,
        e=ErrorInfo(type="JobLeaseExpired", message="").model_dump_json(),
    )
    await _age_job(job_id)
    await _age_run(run_id)
    dispatcher = _Dispatcher()

    assert _mine(await _advances(dispatcher), run_id) == [ResendOutcome.RESENT]
    final = await _advance(run_id, dispatcher)

    assert final.status == PipelineRunStatus.FAILED
    assert final.error.type == "JobLeaseExpired"
