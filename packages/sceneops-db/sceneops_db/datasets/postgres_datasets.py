from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import (
    DatasetRecord,
    DatasetVersionRecord,
    DatasetVersionStatus,
)
from sceneops_db.datasets.models import DatasetModel, DatasetVersionModel
from sceneops_db.utils import enum_to_str


def make_dataset_version_id(dataset_id: str, version: str) -> str:
    return f"{dataset_id}:{version}"


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


class PostgresDatasetVersionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, record: DatasetVersionRecord) -> DatasetVersionRecord:
        model = self._to_model(record)

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord:
        stmt = select(DatasetVersionModel).where(
            DatasetVersionModel.dataset_id == dataset_id,
            DatasetVersionModel.version == version,
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()

        if model is None:
            raise FileNotFoundError(
                f"Dataset version not found: {dataset_id}:{version}"
            )

        return self._to_schema(model)

    async def list(
        self,
        *,
        dataset_id: str,
    ) -> list[DatasetVersionRecord]:
        stmt = (
            select(DatasetVersionModel)
            .where(DatasetVersionModel.dataset_id == dataset_id)
            .order_by(DatasetVersionModel.created_at.desc())
        )

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update(
        self,
        record: DatasetVersionRecord,
    ) -> DatasetVersionRecord:
        model = await self.session.get(DatasetVersionModel, record.id)

        if model is None:
            raise FileNotFoundError(f"Dataset version not found: {record.id}")

        updated = self._to_model(record)

        model.dataset_id = updated.dataset_id
        model.version = updated.version
        model.dataset_type = updated.dataset_type
        model.manifest_uri = updated.manifest_uri
        model.raw_data_uri = updated.raw_data_uri
        model.scene_count = updated.scene_count
        model.sample_count = updated.sample_count
        model.annotation_count = updated.annotation_count
        model.status = updated.status
        model.metadata_ = updated.metadata_

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def upsert(
        self,
        *,
        dataset_id: str,
        version: str,
        dataset_type: str,
        manifest_uri: str | None = None,
        raw_data_uri: str | None = None,
        scene_count: int | None = None,
        sample_count: int | None = None,
        annotation_count: int | None = None,
        status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED,
        metadata: JsonDict | None = None,
    ) -> DatasetVersionRecord:
        version_id = make_dataset_version_id(dataset_id, version)
        dataset_type_value = enum_to_str(dataset_type)
        status_value = enum_to_str(status)

        stmt = (
            insert(DatasetVersionModel)
            .values(
                id=version_id,
                dataset_id=dataset_id,
                version=version,
                dataset_type=dataset_type_value,
                manifest_uri=manifest_uri,
                raw_data_uri=raw_data_uri,
                scene_count=scene_count,
                sample_count=sample_count,
                annotation_count=annotation_count,
                status=status_value,
                metadata_=metadata or {},
            )
            .on_conflict_do_update(
                constraint="uq_dataset_versions_dataset_id_version",
                set_={
                    "dataset_type": dataset_type_value,
                    "manifest_uri": manifest_uri,
                    "raw_data_uri": raw_data_uri,
                    "scene_count": scene_count,
                    "sample_count": sample_count,
                    "annotation_count": annotation_count,
                    "status": status_value,
                    "metadata": metadata or {},
                },
            )
            .returning(DatasetVersionModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    def _to_model(self, record: DatasetVersionRecord) -> DatasetVersionModel:
        return DatasetVersionModel(
            id=record.id,
            dataset_id=record.dataset_id,
            version=record.version,
            dataset_type=enum_to_str(record.dataset_type),
            manifest_uri=record.manifest_uri,
            raw_data_uri=record.raw_data_uri,
            scene_count=record.scene_count,
            sample_count=record.sample_count,
            annotation_count=record.annotation_count,
            status=enum_to_str(record.status),
            metadata_=record.metadata,
        )

    def _to_schema(self, model: DatasetVersionModel) -> DatasetVersionRecord:
        return DatasetVersionRecord.model_validate({
            "id":model.id,
            "dataset_id": model.dataset_id,
            "version": model.version,
            "dataset_type": model.dataset_type,
            "manifest_uri": model.manifest_uri,
            "raw_data_uri": model.raw_data_uri,
            "scene_count": model.scene_count,
            "sample_count": model.sample_count,
            "annotation_count": model.annotation_count,
            "status": model.status,
            "metadata": model.metadata_ or {},
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        })
