from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.enums import DatasetType
from sceneops_core.datasets.schemas.validation import DatasetValidationStatus

from sceneops_db.converters.datasets import (
    dataset_model_to_record,
    dataset_record_to_values,
    dataset_version_model_to_record,
    dataset_version_record_to_values,
    make_dataset_version_id,
)
from sceneops_db.models.datasets import DatasetModel, DatasetVersionModel

from ._utils import apply_pagination, apply_values, enum_value, values_without_none


class PostgresDatasetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, dataset: DatasetRecord) -> DatasetRecord:
        model = DatasetModel(**dataset_record_to_values(dataset))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_model_to_record(model)

    async def upsert(self, dataset: DatasetRecord) -> DatasetRecord:
        existing = await self.get(dataset.dataset_id)
        if existing is None:
            return await self.create(dataset)
        return await self.update(dataset)

    async def get(self, dataset_id: str) -> DatasetRecord | None:
        stmt = select(DatasetModel).where(DatasetModel.dataset_id == dataset_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return dataset_model_to_record(model) if model is not None else None

    async def update(self, dataset: DatasetRecord) -> DatasetRecord:
        stmt = select(DatasetModel).where(DatasetModel.dataset_id == dataset.dataset_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Dataset not found: {dataset.dataset_id}")
        apply_values(model, dataset_record_to_values(dataset))
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_model_to_record(model)

    async def list(
        self,
        *,
        type: DatasetType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRecord]:
        stmt = select(DatasetModel)
        if type is not None:
            stmt = stmt.where(DatasetModel.type == enum_value(type))
        stmt = apply_pagination(
            stmt.order_by(DatasetModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [dataset_model_to_record(m) for m in result.scalars().all()]


class PostgresDatasetVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        model = DatasetVersionModel(**dataset_version_record_to_values(version))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_version_model_to_record(model)

    async def upsert(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        existing = await self.get(
            dataset_id=version.dataset_id, version=version.version
        )
        if existing is None:
            return await self.create(version)
        return await self.update(version)

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None:
        version_id = make_dataset_version_id(dataset_id, version)
        stmt = select(DatasetVersionModel).where(DatasetVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return dataset_version_model_to_record(model) if model is not None else None

    async def update(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        version_id = make_dataset_version_id(version.dataset_id, version.version)
        stmt = select(DatasetVersionModel).where(DatasetVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(
                f"DatasetVersion not found: {version.dataset_id}/{version.version}"
            )
        apply_values(model, dataset_version_record_to_values(version))
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_version_model_to_record(model)

    async def update_scene_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        scene_count: int | None = None,
        sample_count: int | None = None,
        frame_count: int | None = None,
        channels: list[str] | None = None,
        required_channels: list[str] | None = None,
        manifest_uri: str | None = None,
        raw_source_root_uri: str | None = None,
        latest_validation_run_id: str | None = None,
        validation_status: DatasetValidationStatus | None = None,
        should_block_pipeline: bool | None = None,
        validation_report_uri: str | None = None,
        latest_profile_run_id: str | None = None,
        profile_report_uri: str | None = None,
    ) -> DatasetVersionRecord:
        """Partial update of Scene-owned columns only.

        None means "leave untouched" — omitted kwargs never overwrite the
        existing value (including should_block_pipeline=False, which is
        falsy but not None, so it round-trips correctly through
        values_without_none). Never touches episode_count or any generic
        identity/state column.
        """
        model = await self._get_model_or_raise(dataset_id, version)

        scene_values = values_without_none(
            {
                "scene_count": scene_count,
                "sample_count": sample_count,
                "frame_count": frame_count,
                "channels": channels,
                "required_channels": required_channels,
                "manifest_uri": manifest_uri,
                "raw_source_root_uri": raw_source_root_uri,
                "latest_validation_run_id": latest_validation_run_id,
                "validation_status": enum_value(validation_status)
                if validation_status is not None
                else None,
                "should_block_pipeline": should_block_pipeline,
                "validation_report_uri": validation_report_uri,
                "latest_profile_run_id": latest_profile_run_id,
                "profile_report_uri": profile_report_uri,
            }
        )
        apply_values(model, scene_values)
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_version_model_to_record(model)

    async def update_episode_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        episode_count: int | None = None,
    ) -> DatasetVersionRecord:
        """Partial update of Episode-owned columns only. Never touches Scene
        columns or any generic identity/state column."""
        model = await self._get_model_or_raise(dataset_id, version)

        episode_values = values_without_none({"episode_count": episode_count})
        apply_values(model, episode_values)
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_version_model_to_record(model)

    async def _get_model_or_raise(
        self, dataset_id: str, version: str
    ) -> DatasetVersionModel:
        version_id = make_dataset_version_id(dataset_id, version)
        stmt = select(DatasetVersionModel).where(DatasetVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"DatasetVersion not found: {dataset_id}/{version}")
        return model

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]:
        stmt = select(DatasetVersionModel)
        if dataset_id is not None:
            stmt = stmt.where(DatasetVersionModel.dataset_id == dataset_id)
        stmt = apply_pagination(
            stmt.order_by(DatasetVersionModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [dataset_version_model_to_record(m) for m in result.scalars().all()]
