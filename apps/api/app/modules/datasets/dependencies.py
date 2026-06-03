from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ArtifactStoreDep, ApiSettingsDep, DbSessionDep
from app.modules.datasets.scene_building_service import SceneBuildingService
from app.modules.datasets.service import DatasetService
from sceneops_db.datasets import (
    DatasetRepository,
    DatasetVersionRepository,
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)


def get_dataset_repository(
    session: DbSessionDep,
) -> DatasetRepository:
    return PostgresDatasetRepository(session)


DatasetRepositoryDep = Annotated[
    DatasetRepository,
    Depends(get_dataset_repository),
]


def get_dataset_version_repository(
    session: DbSessionDep,
) -> DatasetVersionRepository:
    return PostgresDatasetVersionRepository(session)


DatasetVersionRepositoryDep = Annotated[
    DatasetVersionRepository,
    Depends(get_dataset_version_repository),
]


def get_dataset_service(
    repository: DatasetRepositoryDep,
    version_repository: DatasetVersionRepositoryDep,
) -> DatasetService:
    return DatasetService(
        repository=repository,
        version_repository=version_repository,
    )


DatasetServiceDep = Annotated[
    DatasetService,
    Depends(get_dataset_service),
]


def get_scene_building_service(
    version_repository: DatasetVersionRepositoryDep,
    artifact_store: ArtifactStoreDep,
    settings: ApiSettingsDep,
) -> SceneBuildingService:
    return SceneBuildingService(
        version_repository=version_repository,
        artifact_store=artifact_store,
        root_uri=settings.artifact.dataset_root_uri,
    )


SceneBuildingServiceDep = Annotated[
    SceneBuildingService,
    Depends(get_scene_building_service),
]
