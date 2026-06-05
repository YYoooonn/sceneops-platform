from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

from ._utils import apply_pagination, apply_values, enum_value


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
        pipeline_step_run_id: str | None = None,
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
        if pipeline_step_run_id is not None:
            stmt = stmt.where(JobModel.pipeline_step_run_id == pipeline_step_run_id)
        stmt = apply_pagination(
            stmt.order_by(JobModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [job_model_to_manifest(m) for m in result.scalars().all()]

    async def count_by_status(self) -> dict[str, int]:
        stmt = select(JobModel.status, func.count()).group_by(JobModel.status)
        result = await self._session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}


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
