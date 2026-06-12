from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import ArtifactRepositoryDep
from app.platform.artifacts.service import ArtifactService


def get_artifact_service(repository: ArtifactRepositoryDep) -> ArtifactService:
    return ArtifactService(repository=repository)


ArtifactServiceDep = Annotated[ArtifactService, Depends(get_artifact_service)]
