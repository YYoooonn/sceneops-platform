from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.evaluations.service import EvaluationQueryService
from app.modules.runs.dependencies import EvaluationRunRepositoryDep


def get_evaluation_query_service(
    evaluation_run_repository: EvaluationRunRepositoryDep,
) -> EvaluationQueryService:
    return EvaluationQueryService(
        evaluation_run_repository=evaluation_run_repository,
    )


EvaluationQueryServiceDep = Annotated[
    EvaluationQueryService,
    Depends(get_evaluation_query_service),
]
