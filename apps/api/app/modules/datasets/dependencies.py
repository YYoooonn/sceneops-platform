from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSessionDep
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
