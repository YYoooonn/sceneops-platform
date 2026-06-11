from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.datasets.schemas import (
    CreateDatasetRequest,
    DatasetRecord,
    DatasetVersionRecord,
)
from sceneops_core.datasets.schemas.enums import DatasetStatus, DatasetType
from sceneops_core.runs.schemas import RunType
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.datasets import (
    DatasetRepository,
    DatasetVersionRepository,
)
from sceneops_db.repositories.scenes import SceneRepository, SceneRunRepository

from app.domains.datasets.quality import (
    build_dataset_scene_quality_aggregate,
    build_dataset_version_quality_from_aggregate,
)
from app.domains.datasets.schemas import (
    CreateDatasetVersionBody,
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetSceneQualityListResponse,
    DatasetVersionDetailResponse,
    DatasetVersionListResponse,
    DatasetVersionQualityResponse,
    UpdateDatasetRequest,
    UpdateDatasetVersionRequest,
)
from app.domains.scenes.quality import build_scene_quality
from app.domains.scenes.schemas import SceneListResponse, SceneQualityResponse


class DatasetService:
    def __init__(
        self,
        *,
        repository: DatasetRepository,
        version_repository: DatasetVersionRepository,
        scene_repository: SceneRepository,
        scene_run_repository: SceneRunRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._version_repository = version_repository
        self._scene_repository = scene_repository
        self._scene_run_repository = scene_run_repository
        self._artifact_repository = artifact_repository

    async def list_datasets(
        self,
        *,
        type: DatasetType | None = None,
        status: DatasetStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DatasetListResponse:
        datasets = await self._repository.list(
            type=type, status=status, limit=limit, offset=offset
        )
        return DatasetListResponse(datasets=datasets, count=len(datasets))

    async def create_dataset(
        self, request: CreateDatasetRequest
    ) -> DatasetDetailResponse:
        dataset = await self._repository.upsert(
            DatasetRecord(
                dataset_id=request.dataset_id,
                name=request.name,
                description=request.description,
                type=request.type,
                metadata=request.metadata,
            )
        )
        return DatasetDetailResponse(dataset=dataset)

    async def get_dataset(self, dataset_id: str) -> DatasetDetailResponse | None:
        dataset = await self._repository.get(dataset_id)
        if dataset is None:
            return None
        return DatasetDetailResponse(dataset=dataset)

    async def update_dataset(
        self, dataset_id: str, request: UpdateDatasetRequest
    ) -> DatasetDetailResponse | None:
        existing = await self._repository.get(dataset_id)
        if existing is None:
            return None
        updated = await self._repository.upsert(
            DatasetRecord(
                dataset_id=dataset_id,
                name=request.name,
                description=request.description,
                type=request.type,
                metadata=request.metadata,
            )
        )
        return DatasetDetailResponse(dataset=updated)

    async def list_dataset_versions(
        self,
        dataset_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> DatasetVersionListResponse | None:
        dataset = await self._repository.get(dataset_id)
        if dataset is None:
            return None
        versions = await self._version_repository.list(
            dataset_id=dataset_id, limit=limit, offset=offset
        )
        return DatasetVersionListResponse(versions=versions, count=len(versions))

    async def create_dataset_version(
        self, dataset_id: str, body: CreateDatasetVersionBody
    ) -> DatasetVersionDetailResponse | None:
        dataset = await self._repository.get(dataset_id)
        if dataset is None:
            return None
        version = await self._version_repository.upsert(
            DatasetVersionRecord(
                dataset_id=dataset_id,
                version=body.version,
                status=body.status,
                manifest_uri=body.manifest_uri,
                raw_source_root_uri=body.raw_source_root_uri,
                required_channels=body.required_channels,
                source_dataset_id=body.source_dataset_id,
                source_dataset_version=body.source_dataset_version,
                metadata=body.metadata,
            )
        )
        return DatasetVersionDetailResponse(version=version)

    async def get_dataset_version(
        self, dataset_id: str, version: str
    ) -> DatasetVersionDetailResponse | None:
        record = await self._version_repository.get(
            dataset_id=dataset_id, version=version
        )
        if record is None:
            return None
        return DatasetVersionDetailResponse(version=record)

    async def update_dataset_version(
        self, dataset_id: str, version: str, request: UpdateDatasetVersionRequest
    ) -> DatasetVersionDetailResponse | None:
        existing = await self._version_repository.get(
            dataset_id=dataset_id, version=version
        )
        if existing is None:
            return None
        patched = existing.model_copy(
            update={k: v for k, v in request.model_dump().items() if v is not None}
        )
        updated = await self._version_repository.update(patched)
        return DatasetVersionDetailResponse(version=updated)

    async def get_dataset_version_quality(
        self, dataset_id: str, version: str
    ) -> DatasetVersionQualityResponse | None:
        record = await self._version_repository.get(
            dataset_id=dataset_id, version=version
        )
        if record is None:
            return None

        all_quality = await self._fetch_all_scene_quality(
            dataset_id=dataset_id, version=version
        )
        summary = build_dataset_scene_quality_aggregate(all_quality)

        return build_dataset_version_quality_from_aggregate(
            version=record,
            summary=summary,
        )

    async def list_dataset_version_scenes(
        self, dataset_id: str, version: str, *, limit: int = 100, offset: int = 0
    ) -> SceneListResponse | None:
        record = await self._version_repository.get(
            dataset_id=dataset_id, version=version
        )
        if record is None:
            return None
        scenes = await self._scene_repository.list(
            dataset_id=dataset_id, dataset_version=version, limit=limit, offset=offset
        )
        return SceneListResponse(scenes=scenes, count=len(scenes))

    async def list_scene_quality(
        self,
        *,
        dataset_id: str,
        version: str,
        limit: int,
        offset: int,
    ) -> DatasetSceneQualityListResponse | None:
        record = await self._version_repository.get(
            dataset_id=dataset_id, version=version
        )
        if record is None:
            return None

        all_quality = await self._fetch_all_scene_quality(
            dataset_id=dataset_id, version=version
        )
        summary = build_dataset_scene_quality_aggregate(all_quality)
        paginated = all_quality[offset : offset + limit]

        return DatasetSceneQualityListResponse(
            dataset_id=dataset_id,
            version=version,
            count=len(all_quality),
            limit=limit,
            offset=offset,
            summary=summary,
            scenes=paginated,
        )

    async def list_dataset_version_artifacts(
        self, dataset_id: str, version: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        record = await self._version_repository.get(
            dataset_id=dataset_id, version=version
        )
        if record is None:
            return None
        return await self._artifact_repository.list(
            dataset_id=dataset_id, dataset_version=version, limit=limit, offset=offset
        )

    async def _fetch_all_scene_quality(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> list[SceneQualityResponse]:
        """Fetch all scene quality rows for a dataset version (3 DB queries).

        Shared by get_dataset_version_quality and list_scene_quality so both
        endpoints produce consistent counts from the same data.
        """
        all_scenes = await self._scene_repository.list(
            dataset_id=dataset_id, dataset_version=version, limit=10000, offset=0
        )
        latest_validation = (
            await self._scene_run_repository.list_latest_by_dataset_version(
                dataset_id=dataset_id,
                dataset_version=version,
                run_type=RunType.SCENE_VALIDATION,
            )
        )
        latest_profile = (
            await self._scene_run_repository.list_latest_by_dataset_version(
                dataset_id=dataset_id,
                dataset_version=version,
                run_type=RunType.SCENE_PROFILE,
            )
        )
        return [
            build_scene_quality(
                scene=s,
                validation_run=latest_validation.get(s.scene_id),
                profile_run=latest_profile.get(s.scene_id),
            )
            for s in all_scenes
        ]
