from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.ids.job_events import generate_job_event_id
from sceneops_core.schemas.jobs import (
    JobEventLevel,
    JobEventManifest,
    JobEventType,
)
from sceneops_db.jobs import JobEventModel
from sceneops_db.utils import enum_to_str


class PostgresJobEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(
        self,
        *,
        job_id: str,
        event_type: JobEventType,
        level: JobEventLevel = JobEventLevel.INFO,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobEventManifest:
        event = JobEventManifest(
            event_id=generate_job_event_id(),
            job_id=job_id,
            event_type=event_type,
            level=level,
            message=message,
            payload=payload or {},
        )

        model = self._to_model(event)
        self.session.add(model)

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def list_by_job(self, job_id: str) -> list[JobEventManifest]:
        stmt = (
            select(JobEventModel)
            .where(JobEventModel.job_id == job_id)
            .order_by(JobEventModel.created_at.asc())
        )

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    def _to_model(self, event: JobEventManifest) -> JobEventModel:

        payload = event.payload if isinstance(event.payload, dict) else {}

        return JobEventModel(
            id=event.event_id,
            job_id=event.job_id,
            event_type=enum_to_str(event.event_type),
            level=enum_to_str(event.level),
            message=event.message,
            payload=payload,
        )

    def _to_schema(self, model: JobEventModel) -> JobEventManifest:
        return JobEventManifest.model_validate({
            "event_id": model.id,
            "job_id": model.job_id,
            "event_type": model.event_type,
            "level": model.level,
            "message":model.message,
            "payload": model.payload or {},
            "created_at": model.created_at,
        })
