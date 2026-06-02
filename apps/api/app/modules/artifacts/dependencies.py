from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ArtifactStoreDep
from app.modules.artifacts import ArtifactService
from app.modules.datasets.dependencies import DatasetVersionRepositoryDep
from app.modules.runs.dependencies import (
    EvaluationRunRepositoryDep,
    InferenceRunRepositoryDep,
    ValidationRunRepositoryDep,
)


def get_artifact_service(
    dataset_version_repository: DatasetVersionRepositoryDep,
    inference_run_repository: InferenceRunRepositoryDep,
    evaluation_run_repository: EvaluationRunRepositoryDep,
    validation_run_repository: ValidationRunRepositoryDep,
    artifact_store: ArtifactStoreDep,
) -> ArtifactService:
    return ArtifactService(
        dataset_version_repository=dataset_version_repository,
        inference_run_repository=inference_run_repository,
        evaluation_run_repository=evaluation_run_repository,
        validation_run_repository=validation_run_repository,
        artifact_store=artifact_store,
    )


ArtifactServiceDep = Annotated[
    ArtifactService,
    Depends(get_artifact_service),
]
