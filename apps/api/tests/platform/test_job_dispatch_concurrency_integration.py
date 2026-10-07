"""``JobService.mark_queued`` against concurrent Job transitions, on real PostgreSQL:
a dispatcher that read a Job before another actor changed it must not move the Job
back to QUEUED, and must not dispatch it.

Each test pauses the dispatcher between its read and its write, lets another actor
commit through the same repository calls JobRunner (``claim_for_run``,
``update_owned_run``) and reconciliation (``abandon_if_inactive``) make, then lets
the dispatcher write. The tests commit their own uniquely-identified Jobs into the
disposable database of `make test-integration`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.platform.jobs.dispatch_facade import JobDispatchFacade
from app.platform.jobs.service import JobDispatchConflictError, JobService
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)
from sceneops_core.jobs.schemas import (
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)

_RUNNABLE = {JobStatus.PENDING, JobStatus.QUEUED}


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


class _PausedAfterRead(PostgresJobRepository):
    """Holds the dispatcher after its first read of the Job until released."""

    def __init__(self, session, has_read: asyncio.Event, release: asyncio.Event):
        super().__init__(session)
        self._has_read: asyncio.Event | None = has_read
        self._release = release

    async def get(self, job_id):
        job = await super().get(job_id)
        if self._has_read is not None:
            has_read, self._has_read = self._has_read, None
            has_read.set()
            await self._release.wait()
        return job


def _service(session, repository: PostgresJobRepository) -> JobService:
    return JobService(
        repository=repository,
        event_repository=PostgresJobEventRepository(session),
        artifact_repository=PostgresArtifactRefRepository(session),
    )


async def _committed_job(status: JobStatus, **fields) -> str:
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(
                job_id=job_id,
                type=JobType.REGISTER_ROBOT_RUN,
                status=status,
                execution_key=f"key-{job_id}",
                created_at=fields.pop("created_at", now),
                queued_at=fields.pop("queued_at", now),
                updated_at=now,
                **fields,
            )
        )
        await session.commit()
    return job_id


async def _stale_dispatch(job_id: str, meanwhile) -> JobManifest | BaseException:
    """``mark_queued`` as the dispatch facade runs it (its own transaction,
    committed before the message would be sent), with ``meanwhile`` committed by
    another actor between the dispatcher's read and its write."""
    has_read, release = asyncio.Event(), asyncio.Event()

    async def dispatcher() -> JobManifest | BaseException:
        async with get_async_sessionmaker()() as session:
            repository = _PausedAfterRead(session, has_read, release)
            try:
                queued = await _service(session, repository).mark_queued(job_id)
                await session.commit()
                return queued
            except Exception as exc:  # noqa: BLE001 - the outcome under test
                return exc

    task = asyncio.create_task(dispatcher())
    await has_read.wait()
    await meanwhile()
    release.set()
    return await asyncio.wait_for(task, timeout=10)


async def _claim(job_id: str, worker_id: str) -> JobManifest | None:
    async with get_async_sessionmaker()() as session:
        claimed = await PostgresJobRepository(session).claim_for_run(
            job_id, worker_id=worker_id, runnable_statuses=_RUNNABLE, lease_seconds=60
        )
        await session.commit()
        return claimed


async def _finish(job: JobManifest, status: JobStatus) -> JobManifest | None:
    async with get_async_sessionmaker()() as session:
        saved = await PostgresJobRepository(session).update_owned_run(
            job.model_copy(update={"status": status, "finished_at": utc_now()}),
            lease_generation=job.lease_generation,
        )
        await session.commit()
        return saved


async def _stored(job_id: str) -> JobManifest:
    async with get_async_sessionmaker()() as session:
        job = await PostgresJobRepository(session).get(job_id)
    assert job is not None
    return job


def _refused(outcome: JobManifest | BaseException) -> bool:
    """The stale dispatcher was refused, so it sends no message."""
    return isinstance(outcome, JobDispatchConflictError)


async def test_a_stale_dispatch_does_not_requeue_a_job_a_worker_claimed():
    job_id = await _committed_job(JobStatus.PENDING)
    claims: list[JobManifest | None] = []

    async def worker_claims() -> None:
        claims.append(await _claim(job_id, "worker-1"))

    outcome = await _stale_dispatch(job_id, worker_claims)
    after_dispatch = await _stored(job_id)
    second_claim = await _claim(job_id, "worker-2")
    first_worker_finishes = await _finish(claims[0], JobStatus.SUCCEEDED)

    observed = {
        "dispatcher": type(outcome).__name__,
        "status_after_dispatch": after_dispatch.status.value,
        "worker_after_dispatch": after_dispatch.worker_id,
        "second_claim": second_claim.worker_id if second_claim else None,
        "first_worker_kept_ownership": first_worker_finishes is not None,
    }
    assert claims[0] is not None
    assert _refused(outcome), observed
    assert after_dispatch.status == JobStatus.RUNNING, observed
    assert after_dispatch.worker_id == "worker-1", observed
    assert second_claim is None, observed
    assert first_worker_finishes is not None, observed


async def test_a_stale_dispatch_does_not_requeue_a_finished_job():
    job_id = await _committed_job(JobStatus.PENDING)

    async def worker_runs_to_success() -> None:
        await _finish(await _claim(job_id, "worker-1"), JobStatus.SUCCEEDED)

    outcome = await _stale_dispatch(job_id, worker_runs_to_success)
    after_dispatch = await _stored(job_id)
    second_claim = await _claim(job_id, "worker-2")

    observed = {
        "dispatcher": type(outcome).__name__,
        "status_after_dispatch": after_dispatch.status.value,
        "second_claim": second_claim.worker_id if second_claim else None,
    }
    assert _refused(outcome), observed
    assert after_dispatch.status == JobStatus.SUCCEEDED, observed
    assert second_claim is None, observed


async def test_a_stale_dispatch_does_not_revive_an_abandoned_job():
    stale = utc_now() - timedelta(hours=1)
    job_id = await _committed_job(JobStatus.PENDING, created_at=stale, queued_at=stale)
    abandon_error = ErrorInfo(type="JobAbandoned", message="no progress")

    async def reconciliation_abandons() -> None:
        async with get_async_sessionmaker()() as session:
            abandoned = await PostgresJobRepository(session).abandon_if_inactive(
                job_id,
                type=JobType.REGISTER_ROBOT_RUN,
                inactive_since=utc_now() - timedelta(minutes=15),
                error=abandon_error,
            )
            await session.commit()
        assert abandoned is not None

    outcome = await _stale_dispatch(job_id, reconciliation_abandons)
    after_dispatch = await _stored(job_id)

    observed = {
        "dispatcher": type(outcome).__name__,
        "status_after_dispatch": after_dispatch.status.value,
        "error_after_dispatch": after_dispatch.error,
    }
    assert _refused(outcome), observed
    assert after_dispatch.status == JobStatus.FAILED, observed
    assert after_dispatch.error is not None, observed


async def test_concurrent_retries_of_a_failed_job_spend_one_retry_each():
    """Two retries of one FAILED Job that both read it before either writes: only
    one of them may queue it, or two dispatches share one retry_count."""
    job_id = await _committed_job(JobStatus.FAILED, max_retries=3)

    async def another_retry() -> None:
        async with get_async_sessionmaker()() as session:
            repository = PostgresJobRepository(session)
            await _service(session, repository).mark_queued(job_id)
            await session.commit()

    outcome = await _stale_dispatch(job_id, another_retry)
    after = await _stored(job_id)

    observed = {
        "stale_retry": type(outcome).__name__,
        "status": after.status.value,
        "retry_count": after.retry_count,
    }
    assert _refused(outcome), observed
    assert after.status == JobStatus.QUEUED and after.retry_count == 1, observed


async def test_a_stale_retry_does_not_requeue_a_later_failure():
    """FAILED -> QUEUED -> RUNNING -> FAILED returns to the status the stale retry
    read; the retry it would repeat was already spent."""
    job_id = await _committed_job(JobStatus.FAILED, max_retries=3)

    async def retried_and_failed_again() -> None:
        async with get_async_sessionmaker()() as session:
            repository = PostgresJobRepository(session)
            await _service(session, repository).mark_queued(job_id)
            await session.commit()
        await _finish(await _claim(job_id, "worker-1"), JobStatus.FAILED)

    outcome = await _stale_dispatch(job_id, retried_and_failed_again)
    after = await _stored(job_id)

    observed = {
        "stale_retry": type(outcome).__name__,
        "status": after.status.value,
        "retry_count": after.retry_count,
    }
    assert _refused(outcome), observed
    assert after.status == JobStatus.FAILED and after.retry_count == 1, observed


class _CountingBackend:
    """The job backend: counts the messages it would send."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        self.sent.append(job_id)
        execution_id = f"exec-{uuid.uuid4().hex[:12]}"
        return ExecutionDispatchResult(
            execution_id=execution_id,
            external_id=execution_id,
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
        )


class _BothReadFirst(PostgresJobRepository):
    """Every dispatcher reads the Job before any of them writes."""

    def __init__(self, session, gate: asyncio.Barrier) -> None:
        super().__init__(session)
        self._gate: asyncio.Barrier | None = gate

    async def get(self, job_id):
        job = await super().get(job_id)
        if self._gate is not None:
            gate, self._gate = self._gate, None
            await gate.wait()
        return job


@pytest.mark.parametrize("expected_status", [JobStatus.PENDING, None])
async def test_concurrent_dispatches_of_a_pending_job_send_one_message(
    monkeypatch, expected_status
):
    """Two submissions of one manifest share one PENDING Job and both dispatch it
    (RobotRunRegistrationService passes expected_status=PENDING; POST
    /jobs/{id}/execute passes none). Both read PENDING; one queues and sends, the
    other is refused before it sends."""
    job_id = await _committed_job(JobStatus.PENDING)
    gate = asyncio.Barrier(2)
    monkeypatch.setattr(
        "app.platform.jobs.dispatch_facade.PostgresJobRepository",
        lambda session: _BothReadFirst(session, gate),
    )
    backend = _CountingBackend()
    facade = JobDispatchFacade(
        session_factory=get_async_sessionmaker(), job_backend=backend
    )

    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(
                facade.dispatch(job_id, expected_status=expected_status)
                for _ in range(2)
            ),
            return_exceptions=True,
        ),
        timeout=10,
    )
    async with get_async_sessionmaker()() as session:
        events = await PostgresJobEventRepository(session).list_for_job(
            job_id, type=JobEventType.QUEUED
        )

    observed = {
        "outcomes": [type(o).__name__ for o in outcomes],
        "messages_sent": len(backend.sent),
        "queued_events": len(events),
        "status": (await _stored(job_id)).status.value,
    }
    assert sorted(observed["outcomes"]) == [
        "ExecutionDispatchResult",
        "JobDispatchConflictError",
    ], observed
    assert backend.sent == [job_id], observed
    assert len(events) == 1, observed
    assert observed["status"] == "queued", observed
