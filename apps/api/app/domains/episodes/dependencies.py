from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import EpisodeRepositoryDep, EpisodeRunRepositoryDep
from app.domains.episodes.service import EpisodeService


def get_episode_service(
    repository: EpisodeRepositoryDep,
    run_repository: EpisodeRunRepositoryDep,
) -> EpisodeService:
    return EpisodeService(repository=repository, run_repository=run_repository)


EpisodeServiceDep = Annotated[EpisodeService, Depends(get_episode_service)]
