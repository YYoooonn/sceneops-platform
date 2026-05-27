from __future__ import annotations

from sceneops_core.schemas.datasets import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetVersionDetailResponse,
    DatasetVersionListResponse,
    UpsertDatasetRequest,
    UpsertDatasetVersionRequest,
)
from sceneops_db.datasets import DatasetRepository, DatasetVersionRepository


class DatasetService:
    def __init__(
        self,
        repository: DatasetRepository,
        version_repository: DatasetVersionRepository,
    ) -> None:
        self.repository = repository
        self.version_repository = version_repository

    async def list_datasets(self) -> DatasetListResponse:
        datasets = await self.repository.list()

        return DatasetListResponse(
            datasets=datasets,
            count=len(datasets),
        )

    async def create_dataset(
        self,
        request: CreateDatasetRequest,
    ) -> DatasetDetailResponse:
        dataset = await self.repository.upsert(
            dataset_id=request.id,
            name=request.name,
            dataset_type=request.dataset_type,
            description=request.description,
            metadata=request.metadata,
        )

        return DatasetDetailResponse(dataset=dataset)

    async def get_dataset(
        self,
        dataset_id: str,
    ) -> DatasetDetailResponse | None:
        try:
            dataset = await self.repository.get(dataset_id)
        except FileNotFoundError:
            return None

        return DatasetDetailResponse(dataset=dataset)

    async def upsert_dataset(
        self,
        dataset_id: str,
        request: UpsertDatasetRequest,
    ) -> DatasetDetailResponse:
        dataset = await self.repository.upsert(
            dataset_id=dataset_id,
            name=request.name,
            dataset_type=request.dataset_type,
            description=request.description,
            metadata=request.metadata,
        )

        return DatasetDetailResponse(dataset=dataset)

    async def list_dataset_versions(
        self,
        dataset_id: str,
    ) -> DatasetVersionListResponse | None:
        try:
            await self.repository.get(dataset_id)
        except FileNotFoundError:
            return None

        versions = await self.version_repository.list(
            dataset_id=dataset_id,
        )

        return DatasetVersionListResponse(
            versions=versions,
            count=len(versions),
        )

    async def create_dataset_version(
        self,
        dataset_id: str,
        request: CreateDatasetVersionRequest,
    ) -> DatasetVersionDetailResponse | None:
        try:
            dataset = await self.repository.get(dataset_id)
        except FileNotFoundError:
            return None

        dataset_type = request.dataset_type or dataset.dataset_type

        version = await self.version_repository.upsert(
            dataset_id=dataset_id,
            version=request.version,
            dataset_type=dataset_type,
            manifest_uri=request.manifest_uri,
            raw_data_uri=request.raw_data_uri,
            scene_count=request.scene_count,
            sample_count=request.sample_count,
            annotation_count=request.annotation_count,
            status=request.status,
            metadata=request.metadata,
        )

        return DatasetVersionDetailResponse(version=version)

    async def get_dataset_version(
        self,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionDetailResponse | None:
        try:
            dataset_version = await self.version_repository.get(
                dataset_id=dataset_id,
                version=version,
            )
        except FileNotFoundError:
            return None

        return DatasetVersionDetailResponse(version=dataset_version)

    async def upsert_dataset_version(
        self,
        dataset_id: str,
        version: str,
        request: UpsertDatasetVersionRequest,
    ) -> DatasetVersionDetailResponse | None:
        try:
            dataset = await self.repository.get(dataset_id)
        except FileNotFoundError:
            return None

        dataset_type = request.dataset_type or dataset.dataset_type

        dataset_version = await self.version_repository.upsert(
            dataset_id=dataset_id,
            version=version,
            dataset_type=dataset_type,
            manifest_uri=request.manifest_uri,
            raw_data_uri=request.raw_data_uri,
            scene_count=request.scene_count,
            sample_count=request.sample_count,
            annotation_count=request.annotation_count,
            status=request.status,
            metadata=request.metadata,
        )

        return DatasetVersionDetailResponse(version=dataset_version)
