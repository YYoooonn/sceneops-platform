from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactOwnerType, ArtifactRecord
from sceneops_core.labels.schemas.runs import (
    DatasetAutoLabelRunRecord,
    SceneAutoLabelRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.labels import LabelRunRepository

from app.domains.labels.schemas import (
    DatasetLabelRunListResponse,
    DatasetLabelRunResponse,
    SceneLabelRunListResponse,
    SceneLabelRunResponse,
)


class LabelService:
    def __init__(
        self,
        *,
        repository: LabelRunRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._artifact_repository = artifact_repository

    # --- scene auto-label runs ---

    async def list_scene_runs(
        self,
        *,
        scene_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        labeler_id: str | None = None,
        status: RunStatus | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> SceneLabelRunListResponse:
        records = await self._repository.list(
            type=RunType.SCENE_AUTO_LABEL,
            scene_id=scene_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            labeler_id=labeler_id,
            status=status,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
        runs = [r for r in records if isinstance(r, SceneAutoLabelRunRecord)]
        return SceneLabelRunListResponse(runs=runs, count=len(runs))

    async def get_scene_run(self, run_id: str) -> SceneLabelRunResponse | None:
        record = await self._repository.get(run_id)
        if record is None or not isinstance(record, SceneAutoLabelRunRecord):
            return None
        return SceneLabelRunResponse(run=record)

    async def list_scene_run_artifacts(
        self, run_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        record = await self._repository.get(run_id)
        if record is None or not isinstance(record, SceneAutoLabelRunRecord):
            return None
        return await self._artifact_repository.list(
            owner_type=ArtifactOwnerType.SCENE_AUTO_LABEL_RUN,
            owner_id=run_id,
            limit=limit,
            offset=offset,
        )

    # --- dataset auto-label runs ---

    async def list_dataset_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        labeler_id: str | None = None,
        status: RunStatus | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DatasetLabelRunListResponse:
        records = await self._repository.list(
            type=RunType.DATASET_AUTO_LABEL,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            labeler_id=labeler_id,
            status=status,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
        runs = [r for r in records if isinstance(r, DatasetAutoLabelRunRecord)]
        return DatasetLabelRunListResponse(runs=runs, count=len(runs))

    async def get_dataset_run(self, run_id: str) -> DatasetLabelRunResponse | None:
        record = await self._repository.get(run_id)
        if record is None or not isinstance(record, DatasetAutoLabelRunRecord):
            return None
        return DatasetLabelRunResponse(run=record)

    async def list_dataset_run_artifacts(
        self, run_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        record = await self._repository.get(run_id)
        if record is None or not isinstance(record, DatasetAutoLabelRunRecord):
            return None
        return await self._artifact_repository.list(
            owner_type=ArtifactOwnerType.DATASET_AUTO_LABEL_RUN,
            owner_id=run_id,
            limit=limit,
            offset=offset,
        )
