from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ArtifactStoreDep
from app.core.repositories import (
    ArtifactRepositoryDep,
    EpisodeRepositoryDep,
    EpisodeRunRepositoryDep,
)
from app.domains.episodes.service import EpisodeService


def get_episode_service(
    repository: EpisodeRepositoryDep,
    run_repository: EpisodeRunRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
    artifact_store: ArtifactStoreDep,
) -> EpisodeService:
    return EpisodeService(
        repository=repository,
        run_repository=run_repository,
        artifact_repository=artifact_repository,
        artifact_store=artifact_store,
    )


EpisodeServiceDep = Annotated[EpisodeService, Depends(get_episode_service)]
