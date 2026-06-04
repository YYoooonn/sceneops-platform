from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord, ArtifactRef
from sceneops_db.postgres import PostgresArtifactRefRepository


class ArtifactRecordStore:
    """DB-backed store for artifact ref records.

    Named ArtifactRecordStore to avoid collision with sceneops_storage.ArtifactStore.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresArtifactRefRepository(session)

    async def create(
        self,
        *,
        artifact_id: str,
        ref: ArtifactRef,
        backend: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        scenario_set_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> ArtifactRecord:
        return await self._repo.create(
            artifact_id=artifact_id,
            ref=ref,
            backend=backend,
            owner_type=owner_type,
            owner_id=owner_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            scenario_set_id=scenario_set_id,
            run_id=run_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
        )

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return await self._repo.get(artifact_id)

    async def list(
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
        return await self._repo.list(
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
