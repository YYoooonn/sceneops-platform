from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import (
    ArtifactRepositoryDep,
    DatasetRepositoryDep,
    DatasetVersionRepositoryDep,
    SceneRepositoryDep,
)
from app.domains.datasets.service import DatasetService


def get_dataset_service(
    repository: DatasetRepositoryDep,
    version_repository: DatasetVersionRepositoryDep,
    scene_repository: SceneRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> DatasetService:
    return DatasetService(
        repository=repository,
        version_repository=version_repository,
        scene_repository=scene_repository,
        artifact_repository=artifact_repository,
    )


DatasetServiceDep = Annotated[DatasetService, Depends(get_dataset_service)]
