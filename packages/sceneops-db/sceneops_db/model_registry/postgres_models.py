from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.models.schemas import (
    ModelRecord,
    ModelVersionRecord,
)
from sceneops_core.common.ids import generate_model_version_id
from sceneops_db.model_registry.models import ModelModel, ModelVersionModel
from sceneops_db.utils import enum_to_str, to_jsonable


class PostgresModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, record: ModelRecord) -> ModelRecord:
        metadata = to_jsonable(record.metadata) or {}

        stmt = (
            insert(ModelModel)
            .values(
                id=record.id,
                name=record.name,
                description=record.description,
                metadata_=metadata,
            )
            .on_conflict_do_update(
                index_elements=[ModelModel.id],
                set_={
                    "name": record.name,
                    "description": record.description,
                    "metadata": metadata,
                },
            )
            .returning(ModelModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(self, model_id: str) -> ModelRecord:
        model = await self.session.get(ModelModel, model_id)

        if model is None:
            raise FileNotFoundError(f"Model not found: {model_id}")

        return self._to_schema(model)

    async def list(self) -> list[ModelRecord]:
        stmt = select(ModelModel).order_by(ModelModel.created_at.desc())

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    def _to_schema(self, model: ModelModel) -> ModelRecord:
        return ModelRecord.model_validate(
            {
                "id": model.id,
                "name": model.name,
                "description": model.description,
                "metadata": model.metadata_ or {},
                "created_at": model.created_at,
                "updated_at": model.updated_at,
            }
        )


class PostgresModelVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, record: ModelVersionRecord) -> ModelVersionRecord:
        model_version_id = record.id or generate_model_version_id(
            record.model_id,
            record.version,
        )

        backend = enum_to_str(record.backend)
        status = enum_to_str(record.status)
        runtime = to_jsonable(record.runtime) or {}
        metadata = to_jsonable(record.metadata) or {}

        stmt = (
            insert(ModelVersionModel)
            .values(
                id=model_version_id,
                model_id=record.model_id,
                version=record.version,
                backend=backend,
                status=status,
                model_uri=record.model_uri,
                endpoint_url=record.endpoint_url,
                runtime=runtime,
                metadata_=metadata,
            )
            .on_conflict_do_update(
                constraint="uq_model_versions_model_id_version",
                set_={
                    "backend": backend,
                    "status": status,
                    "model_uri": record.model_uri,
                    "endpoint_url": record.endpoint_url,
                    "runtime": runtime,
                    "metadata": metadata,
                },
            )
            .returning(ModelVersionModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(
        self,
        *,
        model_id: str,
        version: str,
    ) -> ModelVersionRecord:
        stmt = select(ModelVersionModel).where(
            ModelVersionModel.model_id == model_id,
            ModelVersionModel.version == version,
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()

        if model is None:
            raise FileNotFoundError(
                f"Model version not found: {model_id}:{version}"
            )

        return self._to_schema(model)

    async def list(
        self,
        *,
        model_id: str,
    ) -> list[ModelVersionRecord]:
        stmt = (
            select(ModelVersionModel)
            .where(ModelVersionModel.model_id == model_id)
            .order_by(ModelVersionModel.created_at.desc())
        )

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    def _to_schema(self, model: ModelVersionModel) -> ModelVersionRecord:
        return ModelVersionRecord.model_validate(
            {
                "id": model.id,
                "model_id": model.model_id,
                "version": model.version,
                "backend": model.backend,
                "status": model.status,
                "model_uri": model.model_uri,
                "endpoint_url": model.endpoint_url,
                "runtime": model.runtime or {},
                "metadata": model.metadata_ or {},
                "created_at": model.created_at,
                "updated_at": model.updated_at,
            }
        )
