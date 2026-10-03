from __future__ import annotations

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.provenance import UnitSourceKind
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import SceneRecord

from sceneops_db.converters.scenes import (
    SceneRunRecord,
    scene_model_to_record,
    scene_record_to_values,
    scene_run_model_to_record,
    scene_run_record_to_values,
)
from sceneops_db.models.scenes import SceneModel, SceneRunRecordModel
from sceneops_db.repositories.scenes import SceneMembershipSummary

from ._utils import apply_pagination, apply_values, enum_value


class PostgresSceneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, scene_id: str) -> SceneRecord | None:
        model = await self._get_model(scene_id)
        return scene_model_to_record(model) if model is not None else None

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
        stmt = select(SceneModel)
        if dataset_id is not None:
            stmt = stmt.where(SceneModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(SceneModel.dataset_version == dataset_version)
        if source_kind is not None:
            stmt = stmt.where(SceneModel.source_kind == enum_value(source_kind))
        if external_format is not None:
            stmt = stmt.where(SceneModel.external_format == external_format)
        if robot_run_id is not None:
            stmt = stmt.where(SceneModel.robot_run_id == robot_run_id)
        stmt = apply_pagination(
            stmt.order_by(SceneModel.scene_id), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [scene_model_to_record(m) for m in result.scalars().all()]

    async def list_recording_scope(
        self, *, dataset_id: str, dataset_version: str, robot_run_id: str
    ) -> list[SceneRecord]:
        stmt = (
            select(SceneModel)
            .where(SceneModel.dataset_id == dataset_id)
            .where(SceneModel.dataset_version == dataset_version)
            .where(SceneModel.source_kind == UnitSourceKind.RECORDING.value)
            .where(SceneModel.robot_run_id == robot_run_id)
            .order_by(SceneModel.scene_id)
        )
        result = await self._session.execute(stmt)
        return [scene_model_to_record(m) for m in result.scalars().all()]

    async def summarize_membership(
        self, *, dataset_id: str, dataset_version: str
    ) -> SceneMembershipSummary:
        in_version = (
            SceneModel.dataset_id == dataset_id,
            SceneModel.dataset_version == dataset_version,
        )
        totals = (
            await self._session.execute(
                select(
                    func.count(SceneModel.scene_id),
                    func.coalesce(func.sum(SceneModel.keyframe_count), 0),
                    func.coalesce(func.sum(SceneModel.observation_count), 0),
                ).where(*in_version)
            )
        ).one()
        channels = (
            await self._session.execute(
                select(func.jsonb_array_elements_text(SceneModel.observed_channels))
                .where(*in_version)
                .distinct()
            )
        ).scalars()
        return SceneMembershipSummary(
            scene_count=int(totals[0]),
            keyframe_count=int(totals[1]),
            observation_count=int(totals[2]),
            observed_channels=sorted(channels),
        )

    async def insert(self, scene: SceneRecord) -> SceneRecord:
        model = SceneModel(**scene_record_to_values(scene))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return scene_model_to_record(model)

    async def replace_revision(self, scene: SceneRecord) -> SceneRecord:
        """Repoint an existing Scene at a new manifest revision and refresh
        every projection. Identity and membership must not change."""
        model = await self._get_model(scene.scene_id)
        if model is None:
            raise ValueError(f"Scene not found: {scene.scene_id}")
        if (model.dataset_id, model.dataset_version) != (
            scene.dataset_id,
            scene.dataset_version,
        ):
            raise ValueError(
                f"Scene {scene.scene_id} belongs to "
                f"{model.dataset_id}/{model.dataset_version}, not "
                f"{scene.dataset_id}/{scene.dataset_version}"
            )
        apply_values(model, scene_record_to_values(scene))
        await self._session.flush()
        await self._session.refresh(model)
        return scene_model_to_record(model)

    async def delete(self, scene_ids: list[str]) -> int:
        if not scene_ids:
            return 0
        result = await self._session.execute(
            delete(SceneModel).where(SceneModel.scene_id.in_(scene_ids))
        )
        await self._session.flush()
        return result.rowcount or 0

    async def _get_model(self, scene_id: str) -> SceneModel | None:
        result = await self._session.execute(
            select(SceneModel).where(SceneModel.scene_id == scene_id)
        )
        return result.scalar_one_or_none()


class PostgresSceneRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: SceneRunRecord) -> SceneRunRecord:
        model = SceneRunRecordModel(**scene_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
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
        await self._session.refresh(model)
        return scene_run_model_to_record(model)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scene_id: str | None = None,
        manifest_artifact_id: str | None = None,
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
        if manifest_artifact_id is not None:
            stmt = stmt.where(
                SceneRunRecordModel.manifest_artifact_id == manifest_artifact_id
            )
        if dataset_id is not None:
            stmt = stmt.where(SceneRunRecordModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(SceneRunRecordModel.dataset_version == dataset_version)
        if job_id is not None:
            stmt = stmt.where(SceneRunRecordModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(SceneRunRecordModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(
                SceneRunRecordModel.created_at.desc(), SceneRunRecordModel.run_id.desc()
            ),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [scene_run_model_to_record(m) for m in result.scalars().all()]

    async def latest_succeeded_for_current_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        run_type: RunType,
    ) -> dict[str, SceneRunRecord]:
        """For each Scene of the DatasetVersion, its newest succeeded run of
        ``run_type`` that assessed the Scene's *current* manifest revision.
        Runs for any other revision are ignored, so a replaced Scene has no
        entry until its new revision is assessed (ADR-007 §13.4)."""
        stmt = (
            select(SceneRunRecordModel)
            .join(
                SceneModel,
                and_(
                    SceneModel.scene_id == SceneRunRecordModel.scene_id,
                    SceneModel.manifest_artifact_id
                    == SceneRunRecordModel.manifest_artifact_id,
                    SceneModel.manifest_checksum
                    == SceneRunRecordModel.manifest_checksum,
                ),
            )
            .where(SceneModel.dataset_id == dataset_id)
            .where(SceneModel.dataset_version == dataset_version)
            .where(SceneRunRecordModel.type == enum_value(run_type))
            .where(SceneRunRecordModel.status == RunStatus.SUCCEEDED.value)
            .distinct(SceneRunRecordModel.scene_id)
            .order_by(
                SceneRunRecordModel.scene_id,
                SceneRunRecordModel.created_at.desc(),
                SceneRunRecordModel.run_id.desc(),
            )
        )
        result = await self._session.execute(stmt)
        return {
            model.scene_id: scene_run_model_to_record(model)
            for model in result.scalars().all()
        }
