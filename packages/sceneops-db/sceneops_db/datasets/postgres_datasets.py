from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import DatasetRecord
from sceneops_db.datasets.models import DatasetModel
from sceneops_db.utils import enum_to_str


class PostgresDatasetRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, record: DatasetRecord) -> DatasetRecord:
        model = self._to_model(record)

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(self, dataset_id: str) -> DatasetRecord:
        model = await self.session.get(DatasetModel, dataset_id)

        if model is None:
            raise FileNotFoundError(f"Dataset not found: {dataset_id}")

        return self._to_schema(model)

    async def list(self) -> list[DatasetRecord]:
        stmt = select(DatasetModel).order_by(DatasetModel.created_at.desc())
        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update(self, record: DatasetRecord) -> DatasetRecord:
        model = await self.session.get(DatasetModel, record.id)

        if model is None:
            raise FileNotFoundError(f"Dataset not found: {record.id}")

        updated = self._to_model(record)

        model.name = updated.name
        model.dataset_type = updated.dataset_type
        model.description = updated.description
        model.metadata_ = updated.metadata_

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def upsert(
        self,
        *,
        dataset_id: str,
        name: str | None = None,
        dataset_type: str,
        description: str | None = None,
        metadata: JsonDict | None = None,
    ) -> DatasetRecord:
        dataset_type_value = enum_to_str(dataset_type)

        stmt = (
            insert(DatasetModel)
            .values(
                id=dataset_id,
                name=name,
                dataset_type=dataset_type_value,
                description=description,
                metadata_=metadata or {},
            )
            .on_conflict_do_update(
                index_elements=[DatasetModel.id],
                set_={
                    "name": name,
                    "dataset_type": dataset_type_value,
                    "description": description,
                    "metadata": metadata or {},
                },
            )
            .returning(DatasetModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    def _to_model(self, record: DatasetRecord) -> DatasetModel:
        return DatasetModel(
            id=record.id,
            name=record.name,
            dataset_type=enum_to_str(record.dataset_type),
            description=record.description,
            metadata_=record.metadata,
        )

    def _to_schema(self, model: DatasetModel) -> DatasetRecord:
        return DatasetRecord.model_validate({
            "id":model.id,
            "name": model.name,
            "dataset_type":model.dataset_type,
            "description": model.description,
            "metadata": model.metadata_ or {},
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        })
