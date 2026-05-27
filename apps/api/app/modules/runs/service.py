from __future__ import annotations

from sceneops_core.schemas.runs import (
    EvaluationRunDetailResponse,
    EvaluationRunListResponse,
    InferenceRunDetailResponse,
    InferenceRunListResponse,
    RunStatus,
)
from sceneops_db.runs import EvaluationRunRepository, InferenceRunRepository


class RunService:
    def __init__(
        self,
        inference_repository: InferenceRunRepository,
        evaluation_repository: EvaluationRunRepository,
    ) -> None:
        self.inference_repository = inference_repository
        self.evaluation_repository = evaluation_repository

    async def list_inference_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: RunStatus | None = None,
    ) -> InferenceRunListResponse:
        runs = await self.inference_repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            status=status,
        )
        return InferenceRunListResponse(runs=runs, count=len(runs))

    async def get_inference_run(
        self,
        run_id: str,
    ) -> InferenceRunDetailResponse | None:
        try:
            run = await self.inference_repository.get(run_id)
        except FileNotFoundError:
            return None
        return InferenceRunDetailResponse(run=run)

    async def list_evaluation_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        status: RunStatus | None = None,
    ) -> EvaluationRunListResponse:
        runs = await self.evaluation_repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            inference_run_id=inference_run_id,
            status=status,
        )
        return EvaluationRunListResponse(runs=runs, count=len(runs))

    async def get_evaluation_run(
        self,
        evaluation_run_id: str,
    ) -> EvaluationRunDetailResponse | None:
        try:
            run = await self.evaluation_repository.get(evaluation_run_id)
        except FileNotFoundError:
            return None
        return EvaluationRunDetailResponse(run=run)
