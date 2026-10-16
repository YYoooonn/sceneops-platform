from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.scenes.schemas import SceneRecord, SceneRevisionRecord
from sceneops_db.postgres import PostgresSceneRepository
from sceneops_db.repositories import RevisionRegistration


class SceneStore:
    """Scene unit and revision access. ``register_revision`` belongs to the
    Scene registrar (``sceneops_worker.scenes.registration``); no other job
    writes Scene units or revisions."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresSceneRepository(session)

    async def get(self, scene_id: str) -> SceneRecord | None:
        return await self._repo.get(scene_id)

    async def list(
        self, *, robot_run_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[SceneRecord]:
        return await self._repo.list(
            robot_run_id=robot_run_id, limit=limit, offset=offset
        )

    async def get_revision(
        self, *, scene_id: str, manifest_checksum: str
    ) -> SceneRevisionRecord | None:
        return await self._repo.get_revision(
            scene_id=scene_id, manifest_checksum=manifest_checksum
        )

    async def list_revisions(self, scene_id: str) -> list[SceneRevisionRecord]:
        return await self._repo.list_revisions(scene_id)

    async def register_revision(
        self, revision: SceneRevisionRecord
    ) -> RevisionRegistration[SceneRevisionRecord]:
        return await self._repo.register_revision(revision)

    async def get_member_revision(
        self, *, dataset_id: str, dataset_version: str, scene_id: str
    ) -> SceneRevisionRecord | None:
        return await self._repo.get_member_revision(
            dataset_id=dataset_id, dataset_version=dataset_version, scene_id=scene_id
        )

    async def list_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRevisionRecord]:
        return await self._repo.list_member_revisions(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )
