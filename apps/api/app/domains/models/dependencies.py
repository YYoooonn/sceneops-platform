from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import (
    ArtifactRepositoryDep,
    ModelRepositoryDep,
    ModelVersionRepositoryDep,
)
from app.domains.models.service import ModelService


def get_model_service(
    repository: ModelRepositoryDep,
    version_repository: ModelVersionRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> ModelService:
    return ModelService(
        repository=repository,
        version_repository=version_repository,
        artifact_repository=artifact_repository,
    )


ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]
