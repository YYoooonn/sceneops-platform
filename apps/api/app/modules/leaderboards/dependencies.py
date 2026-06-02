from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.evaluations.dependencies import EvaluationQueryServiceDep
from app.modules.leaderboards.service import LeaderboardService


def get_leaderboard_service(
    evaluation_query_service: EvaluationQueryServiceDep,
) -> LeaderboardService:
    return LeaderboardService(
        evaluation_query_service=evaluation_query_service,
    )


LeaderboardServiceDep = Annotated[
    LeaderboardService,
    Depends(get_leaderboard_service),
]
