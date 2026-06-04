from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.enums import DatasetStatus, DatasetType
from sceneops_core.datasets.schemas.validation import DatasetValidationStatus
from sceneops_core.runs.schemas import RunStatus, RunType

from sceneops_db.converters.datasets import (
    DatasetRunRecord,
    dataset_model_to_record,
    dataset_record_to_values,
    dataset_run_model_to_record,
    dataset_run_record_to_values,
    dataset_version_model_to_record,
    dataset_version_record_to_values,
    make_dataset_version_id,
)
from sceneops_db.models.datasets import (
    DatasetModel,
    DatasetRunRecordModel,
    DatasetVersionModel,
)

from ._utils import apply_pagination, apply_values, enum_value, values_without_none


class PostgresDatasetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, dataset: DatasetRecord) -> DatasetRecord:
        model = DatasetModel(**dataset_record_to_values(dataset))
        self._session.add(model)
        await self._session.flush()
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
        return dataset_model_to_record(model)

    async def list(
        self,
        *,
        type: DatasetType | None = None,
        status: DatasetStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRecord]:
        stmt = select(DatasetModel)
        if type is not None:
            stmt = stmt.where(DatasetModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(DatasetModel.status == enum_value(status))
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
        return dataset_version_model_to_record(model)

    async def update_quality_cache(
        self,
        *,
        dataset_id: str,
        version: str,
        latest_validation_run_id: str | None = None,
        validation_status: DatasetValidationStatus | None = None,
        should_block_pipeline: bool | None = None,
        validation_report_uri: str | None = None,
        latest_profile_run_id: str | None = None,
        profile_report_uri: str | None = None,
        latest_distribution_run_id: str | None = None,
        distribution_report_uri: str | None = None,
    ) -> DatasetVersionRecord:
        version_id = make_dataset_version_id(dataset_id, version)
        stmt = select(DatasetVersionModel).where(DatasetVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"DatasetVersion not found: {dataset_id}/{version}")

        cache_values = values_without_none(
            {
                "latest_validation_run_id": latest_validation_run_id,
                "validation_status": enum_value(validation_status)
                if validation_status is not None
                else None,
                "should_block_pipeline": should_block_pipeline,
                "validation_report_uri": validation_report_uri,
                "latest_profile_run_id": latest_profile_run_id,
                "profile_report_uri": profile_report_uri,
                "latest_distribution_run_id": latest_distribution_run_id,
                "distribution_report_uri": distribution_report_uri,
            }
        )
        apply_values(model, cache_values)
        await self._session.flush()
        return dataset_version_model_to_record(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        status: DatasetStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]:
        stmt = select(DatasetVersionModel)
        if dataset_id is not None:
            stmt = stmt.where(DatasetVersionModel.dataset_id == dataset_id)
        if status is not None:
            stmt = stmt.where(DatasetVersionModel.status == enum_value(status))
        stmt = apply_pagination(
            stmt.order_by(DatasetVersionModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [dataset_version_model_to_record(m) for m in result.scalars().all()]


class PostgresDatasetRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: DatasetRunRecord) -> DatasetRunRecord:
        model = DatasetRunRecordModel(**dataset_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        return dataset_run_model_to_record(model)

    async def get(self, run_id: str) -> DatasetRunRecord | None:
        stmt = select(DatasetRunRecordModel).where(
            DatasetRunRecordModel.run_id == run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return dataset_run_model_to_record(model) if model is not None else None

    async def update(self, run: DatasetRunRecord) -> DatasetRunRecord:
        stmt = select(DatasetRunRecordModel).where(
            DatasetRunRecordModel.run_id == run.run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"DatasetRun not found: {run.run_id}")
        apply_values(model, dataset_run_record_to_values(run))
        await self._session.flush()
        return dataset_run_model_to_record(model)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRunRecord]:
        stmt = select(DatasetRunRecordModel)
        if type is not None:
            stmt = stmt.where(DatasetRunRecordModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(DatasetRunRecordModel.status == enum_value(status))
        if dataset_id is not None:
            stmt = stmt.where(DatasetRunRecordModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(DatasetRunRecordModel.dataset_version == dataset_version)
        if job_id is not None:
            stmt = stmt.where(DatasetRunRecordModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(DatasetRunRecordModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(DatasetRunRecordModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [dataset_run_model_to_record(m) for m in result.scalars().all()]
