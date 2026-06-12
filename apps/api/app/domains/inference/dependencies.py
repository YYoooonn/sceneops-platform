from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import ArtifactRepositoryDep, InferenceRunRepositoryDep
from app.domains.inference.service import InferenceService


def get_inference_service(
    repository: InferenceRunRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> InferenceService:
    return InferenceService(
        repository=repository, artifact_repository=artifact_repository
    )


InferenceServiceDep = Annotated[InferenceService, Depends(get_inference_service)]
