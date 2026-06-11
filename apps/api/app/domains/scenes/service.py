from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.scenes.schemas import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneStatus,
)
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)
from sceneops_core.runs.schemas import RunType
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.scenes import SceneRepository, SceneRunRepository

from app.domains.scenes.quality import build_scene_quality
from app.domains.scenes.schemas import (
    SceneDetailResponse,
    SceneListResponse,
    SceneQualityResponse,
)


class SceneService:
    def __init__(
        self,
        *,
        repository: SceneRepository,
        run_repository: SceneRunRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._run_repository = run_repository
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

    async def get_scene_quality(self, scene_id: str) -> SceneQualityResponse | None:
        scene = await self._repository.get(scene_id)
        if scene is None:
            return None

        validation_run = await self._latest_scene_validation_run(scene_id)
        profile_run = await self._latest_scene_profile_run(scene_id)

        return build_scene_quality(
            scene=scene,
            validation_run=validation_run,
            profile_run=profile_run,
        )

    async def list_scene_artifacts(
        self, scene_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        scene = await self._repository.get(scene_id)
        if scene is None:
            return None
        return await self._artifact_repository.list(
            scene_id=scene_id, limit=limit, offset=offset
        )

    async def _latest_scene_validation_run(
        self, scene_id: str
    ) -> SceneValidationRunRecord | None:
        runs = await self._run_repository.list(
            type=RunType.SCENE_VALIDATION,
            scene_id=scene_id,
            limit=1,
        )
        if not runs:
            return None
        run = runs[0]
        return run if isinstance(run, SceneValidationRunRecord) else None

    async def _latest_scene_profile_run(
        self, scene_id: str
    ) -> SceneProfileRunRecord | None:
        runs = await self._run_repository.list(
            type=RunType.SCENE_PROFILE,
            scene_id=scene_id,
            limit=1,
        )
        if not runs:
            return None
        run = runs[0]
        return run if isinstance(run, SceneProfileRunRecord) else None
