from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.models.schemas import (
    ModelBackend,
    ModelRecord,
    ModelTaskType,
    ModelVersionRecord,
    ModelVersionStatus,
)

from sceneops_db.converters.model_registry import (
    make_model_version_id,
    model_model_to_record,
    model_record_to_values,
    model_version_model_to_record,
    model_version_record_to_values,
)
from sceneops_db.models.model_registry import ModelModel, ModelVersionModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, model: ModelRecord) -> ModelRecord:
        orm_model = ModelModel(**model_record_to_values(model))
        self._session.add(orm_model)
        await self._session.flush()
        return model_model_to_record(orm_model)

    async def upsert(self, model: ModelRecord) -> ModelRecord:
        existing = await self.get(model.id)
        if existing is None:
            return await self.create(model)
        return await self.update(model)

    async def get(self, model_id: str) -> ModelRecord | None:
        stmt = select(ModelModel).where(ModelModel.model_id == model_id)
        result = await self._session.execute(stmt)
        orm_model = result.scalar_one_or_none()
        return model_model_to_record(orm_model) if orm_model is not None else None

    async def update(self, model: ModelRecord) -> ModelRecord:
        stmt = select(ModelModel).where(ModelModel.model_id == model.id)
        result = await self._session.execute(stmt)
        orm_model = result.scalar_one_or_none()
        if orm_model is None:
            raise ValueError(f"Model not found: {model.id}")
        apply_values(orm_model, model_record_to_values(model))
        await self._session.flush()
        return model_model_to_record(orm_model)

    async def list(
        self,
        *,
        task_type: ModelTaskType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ModelRecord]:
        stmt = select(ModelModel)
        if task_type is not None:
            stmt = stmt.where(ModelModel.task_type == enum_value(task_type))
        stmt = apply_pagination(
            stmt.order_by(ModelModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [model_model_to_record(m) for m in result.scalars().all()]


class PostgresModelVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, version: ModelVersionRecord) -> ModelVersionRecord:
        model = ModelVersionModel(**model_version_record_to_values(version))
        self._session.add(model)
        await self._session.flush()
        return model_version_model_to_record(model)

    async def upsert(self, version: ModelVersionRecord) -> ModelVersionRecord:
        existing = await self.get(model_id=version.model_id, version=version.version)
        if existing is None:
            return await self.create(version)
        return await self.update(version)

    async def get(
        self,
        *,
        model_id: str,
        version: str,
    ) -> ModelVersionRecord | None:
        version_id = make_model_version_id(model_id, version)
        stmt = select(ModelVersionModel).where(ModelVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model_version_model_to_record(model) if model is not None else None

    async def update(self, version: ModelVersionRecord) -> ModelVersionRecord:
        version_id = make_model_version_id(version.model_id, version.version)
        stmt = select(ModelVersionModel).where(ModelVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(
                f"ModelVersion not found: {version.model_id}/{version.version}"
            )
        apply_values(model, model_version_record_to_values(version))
        await self._session.flush()
        return model_version_model_to_record(model)

    async def list(
        self,
        *,
        model_id: str | None = None,
        task_type: ModelTaskType | None = None,
        backend: ModelBackend | None = None,
        status: ModelVersionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ModelVersionRecord]:
        stmt = select(ModelVersionModel)
        if model_id is not None:
            stmt = stmt.where(ModelVersionModel.model_id == model_id)
        if task_type is not None:
            stmt = stmt.where(ModelVersionModel.task_type == enum_value(task_type))
        if backend is not None:
            stmt = stmt.where(ModelVersionModel.backend == enum_value(backend))
        if status is not None:
            stmt = stmt.where(ModelVersionModel.status == enum_value(status))
        stmt = apply_pagination(
            stmt.order_by(ModelVersionModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [model_version_model_to_record(m) for m in result.scalars().all()]
