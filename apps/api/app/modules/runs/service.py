from __future__ import annotations

from sceneops_core.schemas.datasets import DatasetValidationStatus
from sceneops_core.schemas.runs import (
    DatasetValidationRunDetailResponse,
    DatasetValidationRunListResponse,
    EvaluationRunDetailResponse,
    EvaluationRunListResponse,
    InferenceRunDetailResponse,
    InferenceRunListResponse,
    RunStatus,
)
from sceneops_db.runs import (
    EvaluationRunRepository,
    InferenceRunRepository,
    DatasetValidationRunRepository,
)


class RunService:
    def __init__(
        self,
        inference_repository: InferenceRunRepository,
        evaluation_repository: EvaluationRunRepository,
        validation_repository: DatasetValidationRunRepository,
    ) -> None:
        self.inference_repository = inference_repository
        self.evaluation_repository = evaluation_repository
        self.validation_repository = validation_repository

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

    async def list_validation_run(
        self,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: RunStatus | None = None,
        validation_status: DatasetValidationStatus | None = None,
    ) -> DatasetValidationRunListResponse | None:
        try:
            runs = await self.validation_repository.list(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=status,
                validation_status=validation_status,
            )
        except FileNotFoundError:
            return None
        return DatasetValidationRunListResponse(runs=runs, count=len(runs))

    async def get_validation_run(
        self, validation_run_id: str
    ) -> DatasetValidationRunDetailResponse:
        try:
            run = await self.validation_repository.get(
                validation_run_id=validation_run_id
            )
        except FileNotFoundError:
            return None
        return DatasetValidationRunDetailResponse(run=run)
