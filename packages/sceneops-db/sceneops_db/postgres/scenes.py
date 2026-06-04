from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneRecord,
    SceneStatus,
)

from sceneops_db.converters.scenes import (
    SceneRunRecord,
    scene_model_to_record,
    scene_record_to_values,
    scene_run_model_to_record,
    scene_run_record_to_values,
)
from sceneops_db.models.scenes import SceneModel, SceneRunRecordModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresSceneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, scene: SceneRecord) -> SceneRecord:
        model = SceneModel(**scene_record_to_values(scene))
        self._session.add(model)
        await self._session.flush()
        return scene_model_to_record(model)

    async def upsert(self, scene: SceneRecord) -> SceneRecord:
        existing = await self.get(scene.scene_id)
        if existing is None:
            return await self.create(scene)
        return await self.update(scene)

    async def get(self, scene_id: str) -> SceneRecord | None:
        stmt = select(SceneModel).where(SceneModel.scene_id == scene_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return scene_model_to_record(model) if model is not None else None

    async def update(self, scene: SceneRecord) -> SceneRecord:
        stmt = select(SceneModel).where(SceneModel.scene_id == scene.scene_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Scene not found: {scene.scene_id}")
        apply_values(model, scene_record_to_values(scene))
        await self._session.flush()
        return scene_model_to_record(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: SceneStatus | None = None,
        origin_type: SceneOriginType | None = None,
        generation_method: SceneGenerationMethod | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        stmt = select(SceneModel)
        if dataset_id is not None:
            stmt = stmt.where(SceneModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(SceneModel.dataset_version == dataset_version)
        if status is not None:
            stmt = stmt.where(SceneModel.status == enum_value(status))
        if origin_type is not None:
            stmt = stmt.where(SceneModel.origin_type == enum_value(origin_type))
        if generation_method is not None:
            stmt = stmt.where(
                SceneModel.generation_method == enum_value(generation_method)
            )
        stmt = apply_pagination(
            stmt.order_by(SceneModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [scene_model_to_record(m) for m in result.scalars().all()]


class PostgresSceneRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: SceneRunRecord) -> SceneRunRecord:
        model = SceneRunRecordModel(**scene_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        return scene_run_model_to_record(model)

    async def get(self, run_id: str) -> SceneRunRecord | None:
        stmt = select(SceneRunRecordModel).where(SceneRunRecordModel.run_id == run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return scene_run_model_to_record(model) if model is not None else None

    async def update(self, run: SceneRunRecord) -> SceneRunRecord:
        stmt = select(SceneRunRecordModel).where(
            SceneRunRecordModel.run_id == run.run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"SceneRun not found: {run.run_id}")
        apply_values(model, scene_run_record_to_values(run))
        await self._session.flush()
        return scene_run_model_to_record(model)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scene_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRunRecord]:
        stmt = select(SceneRunRecordModel)
        if type is not None:
            stmt = stmt.where(SceneRunRecordModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(SceneRunRecordModel.status == enum_value(status))
        if scene_id is not None:
            stmt = stmt.where(SceneRunRecordModel.scene_id == scene_id)
        if dataset_id is not None:
            stmt = stmt.where(SceneRunRecordModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(SceneRunRecordModel.dataset_version == dataset_version)
        if job_id is not None:
            stmt = stmt.where(SceneRunRecordModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(SceneRunRecordModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(SceneRunRecordModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [scene_run_model_to_record(m) for m in result.scalars().all()]
