from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import ArtifactRepositoryDep, SceneRepositoryDep
from app.domains.scenes.service import SceneService


def get_scene_service(
    repository: SceneRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> SceneService:
    return SceneService(repository=repository, artifact_repository=artifact_repository)


SceneServiceDep = Annotated[SceneService, Depends(get_scene_service)]
