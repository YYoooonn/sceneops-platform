from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.provenance import UnitSourceKind
from sceneops_core.scenes.schemas import SceneRecord
from sceneops_db.postgres import PostgresSceneRepository
from sceneops_db.repositories import SceneMembershipSummary


class SceneStore:
    """SceneRecord access. ``insert`` / ``replace_revision`` / ``delete``
    belong to the Scene registrar (``sceneops_worker.scenes.registration``)
    and no other job writes Scene membership."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresSceneRepository(session)

    async def get(self, scene_id: str) -> SceneRecord | None:
        return await self._repo.get(scene_id)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        source_kind: UnitSourceKind | None = None,
        external_format: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        return await self._repo.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source_kind=source_kind,
            external_format=external_format,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )

    async def list_recording_scope(
        self, *, dataset_id: str, dataset_version: str, robot_run_id: str
    ) -> list[SceneRecord]:
        return await self._repo.list_recording_scope(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
        )

    async def summarize_membership(
        self, *, dataset_id: str, dataset_version: str
    ) -> SceneMembershipSummary:
        return await self._repo.summarize_membership(
            dataset_id=dataset_id, dataset_version=dataset_version
        )

    async def insert(self, scene: SceneRecord) -> SceneRecord:
        return await self._repo.insert(scene)

    async def replace_revision(self, scene: SceneRecord) -> SceneRecord:
        return await self._repo.replace_revision(scene)

    async def delete(self, scene_ids: list[str]) -> int:
        return await self._repo.delete(scene_ids)
