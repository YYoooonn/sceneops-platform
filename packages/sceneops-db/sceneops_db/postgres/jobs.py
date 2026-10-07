from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
)

from sceneops_db.converters.jobs import (
    job_event_model_to_event,
    job_event_to_values,
    job_manifest_to_values,
    job_model_to_manifest,
)
from sceneops_db.models.jobs import JOB_IN_FLIGHT_PREDICATE, JobEventModel, JobModel
from sceneops_db.repositories.jobs import JobExecutionKeyInFlightError

from ._utils import IN_CLAUSE_CHUNK, apply_pagination, apply_values, enum_value

_ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING)
# A first dispatch, a redispatch of a Job whose message may be lost, a retry.
_QUEUEABLE_STATUSES = (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.FAILED)
_IN_FLIGHT_INDEX = "uq_jobs_execution_key_in_flight"


def _constraint_name(exc: IntegrityError) -> str | None:
    # asyncpg's exception, wrapped by the SQLAlchemy DBAPI adapter.
    return getattr(exc.orig.__cause__, "constraint_name", None)


def _seconds(seconds: float):
    # An interval computed by PostgreSQL, so a lease is measured on the database
    # clock that recovery compares it with, never on a worker's clock.
    return func.make_interval(0, 0, 0, 0, 0, 0, seconds)


# The columns the claiming worker writes while it owns a RUNNING Job (its start
# bookkeeping and its terminal state); identity, params and request-side columns
# are never part of a run's write.
_RUN_OWNED_COLUMNS = (
    "status",
    "locked_at",
    "heartbeat_at",
    "started_at",
    "finished_at",
    "result",
    "error",
    "steps",
    "updated_at",
)


class PostgresJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, job: JobManifest) -> JobManifest:
        """Insert ``job``. Raises ``JobExecutionKeyInFlightError`` when ``job`` is
        in flight and another in-flight Job holds its execution key.

        One ``INSERT ... ON CONFLICT DO NOTHING`` on the in-flight unique index: a
        writer that meets an uncommitted Job with the same key waits for that
        transaction and loses only if it commits, and losing leaves this
        transaction usable. Any other conflict (the job_id primary key) still
        raises. Unset columns take their database defaults."""
        values = {
            column: value
            for column, value in job_manifest_to_values(job).items()
            if value is not None
        }
        stmt = (
            insert(JobModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[JobModel.execution_key],
                index_where=JOB_IN_FLIGHT_PREDICATE,
            )
            .returning(JobModel)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise JobExecutionKeyInFlightError(job.execution_key)
        return job_model_to_manifest(model)

    async def get(self, job_id: str) -> JobManifest | None:
        stmt = select(JobModel).where(JobModel.job_id == job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None

    async def update(self, job: JobManifest) -> JobManifest:
        """Write every field of ``job`` over the stored row, whatever it holds now.
        Not a lifecycle transition: those are the conditional methods below, which
        a writer holding a stale copy cannot use to undo another actor's write."""
        stmt = select(JobModel).where(JobModel.job_id == job.job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Job not found: {job.job_id}")
        apply_values(model, job_manifest_to_values(job))
        await self._session.flush()
        await self._session.refresh(model)
        return job_model_to_manifest(model)

    async def queue_if_unchanged(self, job: JobManifest) -> JobManifest | None:
        """Move ``job`` to QUEUED iff its row still has the status, retry_count and
        lease_generation ``job`` was read with; returns the stored Job, or None
        when this call wrote nothing. Queueing a FAILED Job is a retry and
        increments retry_count. Raises ``JobExecutionKeyInFlightError`` when a
        retry would put a second Job of its execution key in flight.

        One conditional UPDATE evaluated by PostgreSQL against the row as it is
        now, writing only the queueing columns. A dispatcher whose read is stale
        (a worker claimed or finished the Job, reconciliation abandoned it,
        another dispatcher retried it first) therefore changes nothing: it can
        neither move the Job backward nor erase another actor's columns.
        A status recurs only through a way back to an earlier one -- a retry
        (FAILED -> QUEUED, increments retry_count) or lease recovery (RUNNING ->
        QUEUED, after a claim that incremented lease_generation) -- so the three
        columns together never recur. Does not commit."""
        retry = job.status == JobStatus.FAILED
        now = func.now()
        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job.job_id)
            .where(JobModel.status.in_([enum_value(s) for s in _QUEUEABLE_STATUSES]))
            .where(JobModel.status == enum_value(job.status))
            .where(JobModel.retry_count == job.retry_count)
            .where(JobModel.lease_generation == job.lease_generation)
            .values(
                status=enum_value(JobStatus.QUEUED),
                retry_count=JobModel.retry_count + (1 if retry else 0),
                queued_at=now,
                updated_at=now,
            )
            .returning(JobModel)
            .execution_options(populate_existing=True)
        )
        try:
            result = await self._session.execute(stmt)
        except IntegrityError as exc:
            if _constraint_name(exc) == _IN_FLIGHT_INDEX:
                raise JobExecutionKeyInFlightError(job.execution_key) from exc
            raise
        model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None

    async def list(
        self,
        *,
        type: JobType | None = None,
        status: JobStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        pipeline_run_id: str | None = None,
        pipeline_task_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobManifest]:
        stmt = select(JobModel)
        if type is not None:
            stmt = stmt.where(JobModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(JobModel.status == enum_value(status))
        if dataset_id is not None:
            stmt = stmt.where(JobModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(JobModel.dataset_version == dataset_version)
        if pipeline_run_id is not None:
            stmt = stmt.where(JobModel.pipeline_run_id == pipeline_run_id)
        if pipeline_task_run_id is not None:
            stmt = stmt.where(JobModel.pipeline_task_run_id == pipeline_task_run_id)
        stmt = apply_pagination(
            stmt.order_by(JobModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [job_model_to_manifest(m) for m in result.scalars().all()]

    async def count_by_status(self) -> dict[str, int]:
        stmt = select(JobModel.status, func.count()).group_by(JobModel.status)
        result = await self._session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def find_by_execution_key(
        self,
        execution_key: str,
        *,
        statuses: set[JobStatus],
    ) -> JobManifest | None:
        stmt = (
            select(JobModel)
            .where(JobModel.execution_key == execution_key)
            .where(JobModel.status.in_([enum_value(s) for s in statuses]))
            .order_by(JobModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None

    async def list_for_execution_keys(
        self, execution_keys: Sequence[str], *, type: JobType
    ) -> list[JobManifest]:
        """Every Job of ``type`` carrying one of the execution keys, in any
        status, oldest first (ties broken by job_id so the order is total).
        Read-only; unlike ``find_by_execution_key`` it applies no status
        filter and does not stop at the newest row."""
        jobs: list[JobManifest] = []
        unique = sorted(set(execution_keys))
        for start in range(0, len(unique), IN_CLAUSE_CHUNK):
            stmt = (
                select(JobModel)
                .where(JobModel.type == enum_value(type))
                .where(
                    JobModel.execution_key.in_(unique[start : start + IN_CLAUSE_CHUNK])
                )
            )
            result = await self._session.execute(stmt)
            jobs.extend(job_model_to_manifest(m) for m in result.scalars().all())
        jobs.sort(key=lambda job: (job.created_at, job.job_id))
        return jobs

    async def list_dispatch_overdue(
        self, *, older_than_seconds: float, limit: int
    ) -> list[JobManifest]:
        """QUEUED Jobs whose last dispatch (``queued_at``, set by every dispatch
        and redispatch) is older than ``older_than_seconds`` by PostgreSQL's clock,
        oldest first. A QUEUED Job is waiting for a job message; read-only, so it
        cannot tell a lost message from one still waiting in the queue."""
        last_dispatch = func.coalesce(JobModel.queued_at, JobModel.updated_at)
        stmt = (
            select(JobModel)
            .where(JobModel.status == enum_value(JobStatus.QUEUED))
            .where(last_dispatch < func.now() - _seconds(older_than_seconds))
            .order_by(last_dispatch)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [job_model_to_manifest(m) for m in result.scalars().all()]

    async def claim_redispatch(self, job: JobManifest) -> JobManifest | None:
        """Record a new dispatch of a QUEUED Job iff it is still QUEUED with the
        ``queued_at`` it was read with: ``queued_at`` becomes now. Returns the Job,
        or None when it changed (claimed by a worker, dispatched again by someone
        else). One conditional UPDATE, so of concurrent recovery passes exactly
        one sends the message, and the next resend waits a full interval from
        this one. Does not commit."""
        now = func.now()
        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job.job_id)
            .where(JobModel.status == enum_value(JobStatus.QUEUED))
            .where(JobModel.queued_at.is_not_distinct_from(job.queued_at))
            .values(queued_at=now, updated_at=now)
            .returning(JobModel)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None

    async def abandon_if_inactive(
        self,
        job_id: str,
        *,
        type: JobType,
        inactive_since: datetime,
        error: ErrorInfo,
    ) -> JobManifest | None:
        """Move one PENDING / QUEUED / RUNNING Job of ``type`` to FAILED with
        ``error`` iff its newest activity timestamp (created, queued, started
        or heartbeat) is older than ``inactive_since``. Returns the failed Job,
        or None when this call did not change it: the Job is gone, already
        terminal (a concurrent caller won) or has shown activity since the
        caller read it.

        A single conditional UPDATE, so of any number of concurrent callers
        exactly one receives the Job; that winner alone may replace it. The
        predicate is re-evaluated by PostgreSQL against the row as it is now,
        not against what the caller read earlier. Does not commit."""
        last_activity = func.greatest(
            JobModel.created_at,
            func.coalesce(JobModel.queued_at, JobModel.created_at),
            func.coalesce(JobModel.started_at, JobModel.created_at),
            func.coalesce(JobModel.heartbeat_at, JobModel.created_at),
        )
        now = func.now()
        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job_id)
            .where(JobModel.type == enum_value(type))
            .where(JobModel.status.in_([enum_value(s) for s in _ACTIVE_STATUSES]))
            .where(last_activity < inactive_since)
            .values(
                status=enum_value(JobStatus.FAILED),
                error=error.model_dump(mode="json"),
                finished_at=now,
                updated_at=now,
            )
            .returning(JobModel)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None

    async def claim_for_run(
        self,
        job_id: str,
        *,
        worker_id: str,
        runnable_statuses: set[JobStatus],
        lease_seconds: float,
    ) -> JobManifest | None:
        """Claim a runnable Job for one execution: RUNNING, a new lease
        generation, and a lease of ``lease_seconds`` from PostgreSQL's clock.
        Returns the claimed Job, or None when it is not runnable (another
        claim won, or it is terminal). Does not commit.

        The returned ``lease_generation`` identifies this claim and nothing
        else: ``worker_id`` names the Celery message, which a redelivery repeats,
        so it cannot tell two claims apart."""
        now = func.now()

        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job_id)
            .where(JobModel.status.in_([enum_value(s) for s in runnable_statuses]))
            .values(
                status=enum_value(JobStatus.RUNNING),
                worker_id=worker_id,
                lease_generation=JobModel.lease_generation + 1,
                lease_expires_at=now + _seconds(lease_seconds),
                locked_at=now,
                heartbeat_at=now,
                started_at=func.coalesce(JobModel.started_at, now),
                finished_at=None,
                error=None,
                updated_at=now,
            )
            .returning(JobModel)
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return job_model_to_manifest(model) if model is not None else None

    async def update_owned_run(
        self,
        job: JobManifest,
        *,
        lease_generation: int,
    ) -> JobManifest | None:
        """Persist the run-owned fields of ``job`` iff it is still RUNNING under
        the claim ``lease_generation``; returns the stored Job, or None when this
        call wrote nothing.

        One conditional UPDATE evaluated by PostgreSQL against the row as it is
        now. A Job leaves RUNNING exactly once per claim: the worker holding the
        claim makes that transition, and a terminal Job is never rewritten. A
        worker that lost its claim (abandoned, reclaimed after its lease expired
        and claimed again, already terminal) therefore cannot overwrite whatever
        state replaced it. The lease's expiry alone does not end the claim: until
        recovery reclaims the Job, its owner may still finish it. Does not
        commit."""
        values = job_manifest_to_values(job)
        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job.job_id)
            .where(JobModel.status == enum_value(JobStatus.RUNNING))
            .where(JobModel.lease_generation == lease_generation)
            .values(**{column: values[column] for column in _RUN_OWNED_COLUMNS})
            .returning(JobModel)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return job_model_to_manifest(model) if model is not None else None

    async def renew_lease(
        self,
        job_id: str,
        *,
        lease_generation: int,
        lease_seconds: float,
    ) -> datetime | None:
        """Extend the lease of the claim ``lease_generation`` to ``lease_seconds``
        from now and record the heartbeat; returns the new expiry, or None when
        the Job is no longer RUNNING under that claim (the caller has lost it).

        Conditional on the claim, not on the expiry: a lease that has passed but
        was not yet reclaimed is still held, and whichever of this renewal and
        recovery's reclaim reaches the row first decides. Does not commit."""
        now = func.now()
        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job_id)
            .where(JobModel.status == enum_value(JobStatus.RUNNING))
            .where(JobModel.lease_generation == lease_generation)
            .values(
                heartbeat_at=now,
                lease_expires_at=now + _seconds(lease_seconds),
            )
            .returning(JobModel.lease_expires_at)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_expired_leases(self, *, limit: int) -> list[JobManifest]:
        """RUNNING Jobs whose lease passed by PostgreSQL's clock, longest expired
        first. Read-only; acting on one is ``reclaim_expired_lease``."""
        stmt = (
            select(JobModel)
            .where(JobModel.status == enum_value(JobStatus.RUNNING))
            .where(JobModel.lease_expires_at < func.now())
            .order_by(JobModel.lease_expires_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [job_model_to_manifest(m) for m in result.scalars().all()]

    async def reclaim_expired_lease(
        self,
        job_id: str,
        *,
        claim_budget: int,
        error: ErrorInfo,
    ) -> JobManifest | None:
        """End the claim of one RUNNING Job whose lease has passed: back to QUEUED
        while it has been claimed fewer than ``claim_budget`` times, otherwise
        FAILED with ``error``. Returns the Job as written, or None when this call
        changed nothing (not RUNNING, or the lease is held again: renewed, or
        already reclaimed and claimed anew).

        Each branch is one conditional UPDATE re-evaluated by PostgreSQL against
        the row as it is now, and the two predicates exclude each other, so of
        any number of concurrent callers exactly one changes the Job, and a
        renewal that reached the row first wins. ``retry_count`` is untouched:
        it counts retries after failures, not lost workers. Does not commit."""
        now = func.now()
        expired = (
            update(JobModel)
            .where(JobModel.job_id == job_id)
            .where(JobModel.status == enum_value(JobStatus.RUNNING))
            .where(JobModel.lease_expires_at < now)
        )
        result = await self._session.execute(
            expired.where(JobModel.lease_generation < claim_budget)
            .values(
                status=enum_value(JobStatus.QUEUED),
                queued_at=now,
                updated_at=now,
            )
            .returning(JobModel)
        )
        model = result.scalar_one_or_none()
        if model is None:
            result = await self._session.execute(
                expired.where(JobModel.lease_generation >= claim_budget)
                .values(
                    status=enum_value(JobStatus.FAILED),
                    error=error.model_dump(mode="json"),
                    finished_at=now,
                    updated_at=now,
                )
                .returning(JobModel)
            )
            model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None


class PostgresJobEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: JobEvent) -> JobEvent:
        model = JobEventModel(**job_event_to_values(event))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return job_event_model_to_event(model)

    async def get(self, event_id: str) -> JobEvent | None:
        stmt = select(JobEventModel).where(JobEventModel.event_id == event_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return job_event_model_to_event(model) if model is not None else None

    async def list_for_job(
        self,
        job_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]:
        stmt = select(JobEventModel).where(JobEventModel.job_id == job_id)
        if level is not None:
            stmt = stmt.where(JobEventModel.level == enum_value(level))
        if type is not None:
            stmt = stmt.where(JobEventModel.type == enum_value(type))
        stmt = apply_pagination(
            stmt.order_by(JobEventModel.created_at.asc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [job_event_model_to_event(m) for m in result.scalars().all()]

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]:
        stmt = select(JobEventModel).where(
            JobEventModel.pipeline_run_id == pipeline_run_id
        )
        if level is not None:
            stmt = stmt.where(JobEventModel.level == enum_value(level))
        if type is not None:
            stmt = stmt.where(JobEventModel.type == enum_value(type))
        stmt = apply_pagination(
            stmt.order_by(JobEventModel.created_at.asc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [job_event_model_to_event(m) for m in result.scalars().all()]
