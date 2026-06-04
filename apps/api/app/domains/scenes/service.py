from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.scenes.schemas import (
    SceneDetailResponse,
    SceneListResponse,
    SceneGenerationMethod,
    SceneOriginType,
    SceneStatus,
)
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.scenes import SceneRepository


class SceneService:
    def __init__(
        self,
        *,
        repository: SceneRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._artifact_repository = artifact_repository

    async def list_scenes(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: SceneStatus | None = None,
        origin_type: SceneOriginType | None = None,
        generation_method: SceneGenerationMethod | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> SceneListResponse:
        scenes = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            status=status,
            origin_type=origin_type,
            generation_method=generation_method,
            limit=limit,
            offset=offset,
        )
        return SceneListResponse(scenes=scenes, count=len(scenes))

    async def get_scene(self, scene_id: str) -> SceneDetailResponse | None:
        scene = await self._repository.get(scene_id)
        if scene is None:
            return None
        return SceneDetailResponse(scene=scene)

    async def list_scene_artifacts(
        self, scene_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        scene = await self._repository.get(scene_id)
        if scene is None:
            return None
        return await self._artifact_repository.list(
            scene_id=scene_id, limit=limit, offset=offset
        )
