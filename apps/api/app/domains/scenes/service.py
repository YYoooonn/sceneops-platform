from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)
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
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> SceneListResponse:
        scenes = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
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

        validation_run = await self._latest_current_revision_run(
            scene, RunType.SCENE_VALIDATION, SceneValidationRunRecord
        )
        profile_run = await self._latest_current_revision_run(
            scene, RunType.SCENE_PROFILE, SceneProfileRunRecord
        )

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

    async def _latest_current_revision_run(self, scene: SceneRecord, run_type, cls):
        """Newest succeeded run of ``run_type`` that assessed the Scene's
        current manifest revision."""
        runs = await self._run_repository.list(
            type=run_type,
            status=RunStatus.SUCCEEDED,
            scene_id=scene.scene_id,
            manifest_artifact_id=scene.manifest_artifact_id,
            limit=20,
        )
        for run in runs:
            if isinstance(run, cls) and run.assessed(
                manifest_artifact_id=scene.manifest_artifact_id,
                manifest_checksum=scene.manifest_checksum,
            ):
                return run
        return None
