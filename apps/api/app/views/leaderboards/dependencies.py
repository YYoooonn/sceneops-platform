from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import EvaluationRunRepositoryDep
from app.views.leaderboards.service import LeaderboardService


def get_leaderboard_service(
    repository: EvaluationRunRepositoryDep,
) -> LeaderboardService:
    return LeaderboardService(repository=repository)


LeaderboardServiceDep = Annotated[LeaderboardService, Depends(get_leaderboard_service)]
