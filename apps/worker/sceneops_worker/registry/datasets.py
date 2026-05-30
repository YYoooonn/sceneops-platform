from __future__ import annotations

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import (
    DatasetVersionRecord,
    DatasetVersionStatus,
)
from sceneops_db.datasets import PostgresDatasetVersionRepository
from sceneops_db.session import async_session_scope


class DatasetRegistryStore:
    async def get_version(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetVersionRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetVersionRepository(session)
            return await repository.get(
                dataset_id=dataset_id,
                version=dataset_version,
            )

    async def upsert_version(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        dataset_type: str,
        manifest_uri: str | None = None,
        source_uri: str | None = None,
        scene_count: int | None = None,
        sample_count: int | None = None,
        annotation_count: int | None = None,
        status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED,
        metadata: JsonDict | None = None,
    ) -> DatasetVersionRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetVersionRepository(session)
            return await repository.upsert(
                dataset_id=dataset_id,
                version=dataset_version,
                dataset_type=dataset_type,
                manifest_uri=manifest_uri,
                source_uri=source_uri,
                scene_count=scene_count,
                sample_count=sample_count,
                annotation_count=annotation_count,
                status=status,
                metadata=metadata,
            )
