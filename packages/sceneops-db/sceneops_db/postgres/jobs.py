from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
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
from sceneops_db.models.jobs import JobEventModel, JobModel

from ._utils import IN_CLAUSE_CHUNK, apply_pagination, apply_values, enum_value

_ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING)


class PostgresJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, job: JobManifest) -> JobManifest:
        model = JobModel(**job_manifest_to_values(job))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return job_model_to_manifest(model)

    async def get(self, job_id: str) -> JobManifest | None:
        stmt = select(JobModel).where(JobModel.job_id == job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return job_model_to_manifest(model) if model is not None else None

    async def update(self, job: JobManifest) -> JobManifest:
        stmt = select(JobModel).where(JobModel.job_id == job.job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Job not found: {job.job_id}")
        apply_values(model, job_manifest_to_values(job))
        await self._session.flush()
        await self._session.refresh(model)
        return job_model_to_manifest(model)

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
    ) -> JobManifest | None:
        now = func.now()

        stmt = (
            update(JobModel)
            .where(JobModel.job_id == job_id)
            .where(JobModel.status.in_([enum_value(s) for s in runnable_statuses]))
            .values(
                status=enum_value(JobStatus.RUNNING),
                worker_id=worker_id,
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
