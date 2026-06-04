from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactOwnerType, ArtifactRecord
from sceneops_core.runs.schemas import RunStatus
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.inference import InferenceRunRepository

from app.domains.inference.schemas import (
    InferenceMetricsResponse,
    InferenceRunListResponse,
    InferenceRunResponse,
)


class InferenceService:
    def __init__(
        self,
        *,
        repository: InferenceRunRepository,
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
        status: RunStatus | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> InferenceRunListResponse:
        runs = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            status=status,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
        return InferenceRunListResponse(runs=runs, count=len(runs))

    async def get_run(self, inference_run_id: str) -> InferenceRunResponse | None:
        run = await self._repository.get(inference_run_id)
        if run is None:
            return None
        return InferenceRunResponse(run=run)

    async def get_run_metrics(
        self, inference_run_id: str
    ) -> InferenceMetricsResponse | None:
        run = await self._repository.get(inference_run_id)
        if run is None:
            return None
        return InferenceMetricsResponse(
            inference_run_id=inference_run_id,
            metrics=run.metrics,
        )

    async def list_run_artifacts(
        self, inference_run_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        run = await self._repository.get(inference_run_id)
        if run is None:
            return None
        return await self._artifact_repository.list(
            owner_type=ArtifactOwnerType.INFERENCE_RUN,
            owner_id=inference_run_id,
            limit=limit,
            offset=offset,
        )
