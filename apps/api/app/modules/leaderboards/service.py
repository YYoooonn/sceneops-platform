from __future__ import annotations

from app.modules.evaluations.service import EvaluationQueryService
from sceneops_core.evaluations import LeaderboardSortBy


class LeaderboardService:
    def __init__(
        self,
        *,
        evaluation_query_service: EvaluationQueryService,
    ) -> None:
        self.evaluation_query_service = evaluation_query_service

    async def detection_leaderboard(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        sort_by: LeaderboardSortBy = LeaderboardSortBy.PRECISION,
        evaluator_id: str | None = "center-distance",
        limit: int | None = 50,
    ):
        return await self.evaluation_query_service.detection_leaderboard(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            sort_by=sort_by,
            evaluator_id=evaluator_id,
            limit=limit,
        )
