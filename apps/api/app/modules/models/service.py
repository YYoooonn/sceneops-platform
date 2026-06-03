from __future__ import annotations

from sceneops_core.models.schemas import (
    CreateModelRequest,
    CreateModelVersionRequest,
    ModelDetailResponse,
    ModelListResponse,
    ModelRecord,
    ModelVersionDetailResponse,
    ModelVersionListResponse,
    ModelVersionRecord,
    UpsertModelRequest,
)
from sceneops_core.ids import generate_model_version_id
from sceneops_db.model_registry import ModelRepository, ModelVersionRepository


class ModelService:
    def __init__(
        self,
        *,
        repository: ModelRepository,
        version_repository: ModelVersionRepository,
    ) -> None:
        self.repository = repository
        self.version_repository = version_repository

    async def list_models(self) -> ModelListResponse:
        models = await self.repository.list()
        return ModelListResponse(models=models, count=len(models))

    async def create_model(self, request: CreateModelRequest) -> ModelDetailResponse:
        model = await self.repository.upsert(
            ModelRecord(
                id=request.model_id,
                name=request.name,
                description=request.description,
                metadata=request.metadata,
            )
        )
        return ModelDetailResponse(model=model)

    async def get_model(self, model_id: str) -> ModelDetailResponse | None:
        try:
            model = await self.repository.get(model_id)
        except FileNotFoundError:
            return None
        return ModelDetailResponse(model=model)

    async def upsert_model(
        self,
        model_id: str,
        request: UpsertModelRequest,
    ) -> ModelDetailResponse:
        model = await self.repository.upsert(
            ModelRecord(
                id=model_id,
                name=request.name,
                description=request.description,
                metadata=request.metadata,
            )
        )
        return ModelDetailResponse(model=model)

    async def list_model_versions(
        self,
        model_id: str,
    ) -> ModelVersionListResponse:
        versions = await self.version_repository.list(model_id=model_id)
        return ModelVersionListResponse(versions=versions, count=len(versions))

    async def create_model_version(
        self,
        model_id: str,
        request: CreateModelVersionRequest,
    ) -> ModelVersionDetailResponse:
        version = await self.version_repository.upsert(
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
        self,
        model_id: str,
        version: str,
    ) -> ModelVersionDetailResponse | None:
        try:
            record = await self.version_repository.get(
                model_id=model_id,
                version=version,
            )
        except FileNotFoundError:
            return None
        return ModelVersionDetailResponse(version=record)
