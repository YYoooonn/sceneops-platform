from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactOwnerType, ArtifactRecord
from sceneops_core.common.ids import generate_model_version_id
from sceneops_core.models.schemas import (
    CreateModelRequest,
    CreateModelVersionRequest,
    ModelBackend,
    ModelRecord,
    ModelTaskType,
    ModelVersionRecord,
    ModelVersionStatus,
)
from app.domains.models.schemas import (
    ModelDetailResponse,
    ModelListResponse,
    ModelVersionDetailResponse,
    ModelVersionListResponse,
)
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.model_registry import (
    ModelRepository,
    ModelVersionRepository,
)


class ModelService:
    def __init__(
        self,
        *,
        repository: ModelRepository,
        version_repository: ModelVersionRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._version_repository = version_repository
        self._artifact_repository = artifact_repository

    async def list_models(
        self,
        *,
        task_type: ModelTaskType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ModelListResponse:
        models = await self._repository.list(
            task_type=task_type, limit=limit, offset=offset
        )
        return ModelListResponse(models=models, count=len(models))

    async def create_model(self, request: CreateModelRequest) -> ModelDetailResponse:
        model = await self._repository.upsert(
            ModelRecord(
                model_id=request.model_id,
                name=request.name,
                description=request.description,
                metadata=request.metadata,
            )
        )
        return ModelDetailResponse(model=model)

    async def get_model(self, model_id: str) -> ModelDetailResponse | None:
        model = await self._repository.get(model_id)
        if model is None:
            return None
        return ModelDetailResponse(model=model)

    async def list_model_versions(
        self,
        model_id: str,
        *,
        task_type: ModelTaskType | None = None,
        backend: ModelBackend | None = None,
        status: ModelVersionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ModelVersionListResponse:
        versions = await self._version_repository.list(
            model_id=model_id,
            task_type=task_type,
            backend=backend,
            status=status,
            limit=limit,
            offset=offset,
        )
        return ModelVersionListResponse(versions=versions, count=len(versions))

    async def create_model_version(
        self, model_id: str, request: CreateModelVersionRequest
    ) -> ModelVersionDetailResponse | None:
        model = await self._repository.get(model_id)
        if model is None:
            return None
        version = await self._version_repository.upsert(
            ModelVersionRecord(
                id=generate_model_version_id(model_id, request.version),
                model_id=model_id,
                version=request.version,
                backend=request.backend,
                status=request.status,
                model_uri=request.model_uri,
                endpoint_url=request.endpoint_url,
                runtime=request.runtime,
                metadata=request.metadata,
            )
        )
        return ModelVersionDetailResponse(version=version)

    async def get_model_version(
        self, model_id: str, version: str
    ) -> ModelVersionDetailResponse | None:
        record = await self._version_repository.get(model_id=model_id, version=version)
        if record is None:
            return None
        return ModelVersionDetailResponse(version=record)

    async def list_model_version_artifacts(
        self, model_id: str, version: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        record = await self._version_repository.get(model_id=model_id, version=version)
        if record is None:
            return None
        return await self._artifact_repository.list(
            owner_type=ArtifactOwnerType.MODEL_VERSION,
            owner_id=record.id,
            limit=limit,
            offset=offset,
        )
