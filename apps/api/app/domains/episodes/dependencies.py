from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import EpisodeRepositoryDep
from app.domains.episodes.service import EpisodeService


def get_episode_service(repository: EpisodeRepositoryDep) -> EpisodeService:
    return EpisodeService(repository=repository)


EpisodeServiceDep = Annotated[EpisodeService, Depends(get_episode_service)]
