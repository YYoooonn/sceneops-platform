from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSessionDep
from app.modules.models.service import ModelService
from sceneops_db.model_registry import (
    ModelRepository,
    ModelVersionRepository,
    PostgresModelRepository,
    PostgresModelVersionRepository,
)


def get_model_repository(
    session: DbSessionDep,
) -> ModelRepository:
    return PostgresModelRepository(session)


ModelRepositoryDep = Annotated[
    ModelRepository,
    Depends(get_model_repository),
]


def get_model_version_repository(
    session: DbSessionDep,
) -> ModelVersionRepository:
    return PostgresModelVersionRepository(session)


ModelVersionRepositoryDep = Annotated[
    ModelVersionRepository,
    Depends(get_model_version_repository),
]


def get_model_service(
    repository: ModelRepositoryDep,
    version_repository: ModelVersionRepositoryDep,
) -> ModelService:
    return ModelService(
        repository=repository,
        version_repository=version_repository,
    )


ModelServiceDep = Annotated[
    ModelService,
    Depends(get_model_service),
]
