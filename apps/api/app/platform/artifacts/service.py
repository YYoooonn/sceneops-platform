from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_db.repositories.artifacts import ArtifactRepository


class ArtifactService:
    def __init__(self, *, repository: ArtifactRepository) -> None:
        self._repository = repository

    async def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return await self._repository.get(artifact_id)

    async def list_artifacts(
        self,
        *,
        kind: ArtifactKind | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        scenario_set_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArtifactRecord]:
        return await self._repository.list(
            kind=kind,
            owner_type=owner_type,
            owner_id=owner_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            scenario_set_id=scenario_set_id,
            run_id=run_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
