from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord, ArtifactRef

from sceneops_db.converters.artifacts import (
    artifact_ref_model_to_record,
    artifact_ref_to_values_with_owner,
)
from sceneops_db.models.artifacts import ArtifactModel

from ._utils import apply_pagination, enum_value


class PostgresArtifactRefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        values = artifact_ref_to_values_with_owner(
            ref,
            artifact_id=artifact_id,
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
        model = ArtifactModel(**values)
        self._session.add(model)
        await self._session.flush()
        return artifact_ref_model_to_record(model)

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        stmt = select(ArtifactModel).where(ArtifactModel.artifact_id == artifact_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return artifact_ref_model_to_record(model) if model is not None else None

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
        stmt = select(ArtifactModel)
        if kind is not None:
            stmt = stmt.where(ArtifactModel.kind == enum_value(kind))
        if owner_type is not None:
            stmt = stmt.where(ArtifactModel.owner_type == owner_type)
        if owner_id is not None:
            stmt = stmt.where(ArtifactModel.owner_id == owner_id)
        if dataset_id is not None:
            stmt = stmt.where(ArtifactModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(ArtifactModel.dataset_version == dataset_version)
        if scene_id is not None:
            stmt = stmt.where(ArtifactModel.scene_id == scene_id)
        if scenario_set_id is not None:
            stmt = stmt.where(ArtifactModel.scenario_set_id == scenario_set_id)
        if run_id is not None:
            stmt = stmt.where(ArtifactModel.run_id == run_id)
        if job_id is not None:
            stmt = stmt.where(ArtifactModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(ArtifactModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(ArtifactModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [artifact_ref_model_to_record(m) for m in result.scalars().all()]
