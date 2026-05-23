from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.ids.job_events import generate_job_event_id
from sceneops_core.schemas.jobs import (
    JobEventLevel,
    JobEventManifest,
    JobEventType,
)
from sceneops_core.time import utc_now_iso
from sceneops_db.jobs import JobEventModel


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
        now = utc_now_iso()

        event = JobEventManifest(
            eventId=generate_job_event_id(),
            jobId=job_id,
            eventType=event_type,
            level=level,
            message=message,
            payload=payload or {},
            createdAt=now,
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
        data = event.model_dump(mode="json")

        return JobEventModel(
            id=data["eventId"],
            job_id=data["jobId"],
            event_type=self._enum_to_str(data["eventType"]),
            level=self._enum_to_str(data["level"]),
            message=data.get("message"),
            payload=data.get("payload") or {},
            created_at=self._extract_datetime(data.get("createdAt")),
        )

    def _to_schema(self, model: JobEventModel) -> JobEventManifest:
        created_at = model.created_at

        return JobEventManifest(
            eventId=model.id,
            jobId=model.job_id,
            eventType=JobEventType(model.event_type),
            level=JobEventLevel(model.level),
            message=model.message,
            payload=model.payload or {},
            createdAt=created_at.isoformat() if created_at else utc_now_iso(),
        )

    def _extract_datetime(self, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value

        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        return None

    def _enum_to_str(self, value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)
