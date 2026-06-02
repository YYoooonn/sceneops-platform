from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from sceneops_core.evaluations import (
    EvaluationLeaderboardResponse,
    LeaderboardSortBy,
)

from app.modules.leaderboards.dependencies import LeaderboardServiceDep

router = APIRouter(prefix="/leaderboards", tags=["leaderboards"])


@router.get("/detection", response_model=EvaluationLeaderboardResponse)
async def detection_leaderboard(
    *,
    dataset_id: str,
    dataset_version: str,
    sort_by: LeaderboardSortBy = LeaderboardSortBy.PRECISION,
    evaluator_id: str | None = "center-distance",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    service: LeaderboardServiceDep,
) -> EvaluationLeaderboardResponse:
    return await service.detection_leaderboard(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        sort_by=sort_by,
        evaluator_id=evaluator_id,
        limit=limit,
    )
