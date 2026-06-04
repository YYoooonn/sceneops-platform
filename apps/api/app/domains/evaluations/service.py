from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactOwnerType, ArtifactRecord
from sceneops_core.evaluations.schemas.enums import EvaluationTaskType
from sceneops_core.runs.schemas import RunStatus
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.evaluations import EvaluationRunRepository

from app.domains.evaluations.schemas import (
    EvaluationMetricsResponse,
    EvaluationRunListResponse,
    EvaluationRunResponse,
)


class EvaluationService:
    def __init__(
        self,
        *,
        repository: EvaluationRunRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._artifact_repository = artifact_repository

    async def list_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        task_type: EvaluationTaskType | None = None,
        evaluator_id: str | None = None,
        status: RunStatus | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> EvaluationRunListResponse:
        runs = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            inference_run_id=inference_run_id,
            task_type=task_type,
            evaluator_id=evaluator_id,
            status=status,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
        return EvaluationRunListResponse(runs=runs, count=len(runs))

    async def get_run(self, evaluation_run_id: str) -> EvaluationRunResponse | None:
        run = await self._repository.get(evaluation_run_id)
        if run is None:
            return None
        return EvaluationRunResponse(run=run)

    async def get_run_metrics(
        self, evaluation_run_id: str
    ) -> EvaluationMetricsResponse | None:
        run = await self._repository.get(evaluation_run_id)
        if run is None:
            return None
        if not run.summary and not run.metrics and not run.class_metrics:
            return None
        return EvaluationMetricsResponse(
            evaluation_run_id=evaluation_run_id,
            summary=run.summary,
            metrics=run.metrics,
            class_metrics=run.class_metrics,
        )

    async def list_run_artifacts(
        self, evaluation_run_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        run = await self._repository.get(evaluation_run_id)
        if run is None:
            return None
        return await self._artifact_repository.list(
            owner_type=ArtifactOwnerType.EVALUATION_RUN,
            owner_id=evaluation_run_id,
            limit=limit,
            offset=offset,
        )
