from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import ArtifactRepositoryDep, EvaluationRunRepositoryDep
from app.domains.evaluations.service import EvaluationService


def get_evaluation_service(
    repository: EvaluationRunRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> EvaluationService:
    return EvaluationService(
        repository=repository, artifact_repository=artifact_repository
    )


EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
