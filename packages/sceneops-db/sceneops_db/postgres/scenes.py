from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import SceneRecord, SceneRevisionRecord

from sceneops_db.converters.datasets import make_dataset_version_id
from sceneops_db.converters.scenes import (
    SceneRunRecord,
    scene_model_to_record,
    scene_revision_model_to_record,
    scene_revision_to_values,
    scene_run_model_to_record,
    scene_run_record_to_values,
    scene_unit_to_values,
)
from sceneops_db.models.datasets import DatasetVersionMemberModel
from sceneops_db.models.scenes import (
    SceneModel,
    SceneRevisionModel,
    SceneRunRecordModel,
)
from sceneops_db.repositories.revisions import RevisionRegistration

from ._revisions import register_unit_revision
from ._utils import IN_CLAUSE_CHUNK, apply_pagination, apply_values, enum_value


class PostgresSceneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- units ------------------------------------------------------------------

    async def get(self, scene_id: str) -> SceneRecord | None:
        model = (
            await self._session.execute(
                select(SceneModel).where(SceneModel.scene_id == scene_id)
            )
        ).scalar_one_or_none()
        return scene_model_to_record(model) if model is not None else None

    async def list(
        self,
        *,
        robot_run_id: str | None = None,
        content_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        stmt = select(SceneModel)
        if robot_run_id is not None:
            stmt = stmt.where(SceneModel.robot_run_id == robot_run_id)
        if content_id is not None:
            stmt = stmt.where(SceneModel.content_id == content_id)
        stmt = apply_pagination(
            stmt.order_by(SceneModel.scene_id), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [scene_model_to_record(m) for m in result.scalars().all()]

    # --- revisions --------------------------------------------------------------

    async def get_revision(
        self, *, scene_id: str, manifest_checksum: str
    ) -> SceneRevisionRecord | None:
        rows = await self._revisions(
            SceneRevisionModel.scene_id == scene_id,
            SceneRevisionModel.manifest_checksum == manifest_checksum,
        )
        return rows[0] if rows else None

    async def list_revisions(self, scene_id: str) -> list[SceneRevisionRecord]:
        return await self._revisions(SceneRevisionModel.scene_id == scene_id)

    async def get_revisions(
        self, keys: Sequence[tuple[str, str]]
    ) -> list[SceneRevisionRecord]:
        unique = sorted(set(keys))
        found: list[SceneRevisionRecord] = []
        for start in range(0, len(unique), IN_CLAUSE_CHUNK):
            found += await self._revisions(
                tuple_(
                    SceneRevisionModel.scene_id, SceneRevisionModel.manifest_checksum
                ).in_(unique[start : start + IN_CLAUSE_CHUNK])
            )
        return found

    async def list_source_revisions(
        self, *, source_kind: str, source_id: str, producer_fingerprint: str
    ) -> list[SceneRevisionRecord]:
        source_column = (
            SceneModel.robot_run_id
            if source_kind == "recording"
            else SceneModel.content_id
        )
        return await self._revisions(
            SceneModel.source_kind == source_kind,
            source_column == source_id,
            SceneRevisionModel.producer_fingerprint == producer_fingerprint,
        )

    async def register_revision(
        self, revision: SceneRevisionRecord
    ) -> RevisionRegistration[SceneRevisionRecord]:
        return await register_unit_revision(
            self._session,
            unit_model=SceneModel,
            revision_model=SceneRevisionModel,
            id_column="scene_id",
            unit_values=scene_unit_to_values(revision),
            revision_values=scene_revision_to_values(revision),
            candidate=revision,
            list_revisions=self.list_revisions,
        )

    # --- the revisions a DatasetVersion pins -----------------------------------

    async def get_member_revision(
        self, *, dataset_id: str, dataset_version: str, scene_id: str
    ) -> SceneRevisionRecord | None:
        rows = await self._revisions(
            DatasetVersionMemberModel.scene_id == scene_id,
            dataset_version_id=make_dataset_version_id(dataset_id, dataset_version),
        )
        return rows[0] if rows else None

    async def list_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRevisionRecord]:
        conditions = (
            [] if robot_run_id is None else [SceneModel.robot_run_id == robot_run_id]
        )
        return await self._revisions(
            *conditions,
            dataset_version_id=make_dataset_version_id(dataset_id, dataset_version),
            limit=limit,
            offset=offset,
        )

    async def _revisions(
        self,
        *conditions: Any,
        dataset_version_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SceneRevisionRecord]:
        stmt = select(SceneRevisionModel, SceneModel).join(
            SceneModel, SceneModel.scene_id == SceneRevisionModel.scene_id
        )
        if dataset_version_id is not None:
            stmt = stmt.join(*_pinned_by(dataset_version_id))
        stmt = stmt.where(*conditions).order_by(
            SceneRevisionModel.scene_id, SceneRevisionModel.manifest_checksum
        )
        if limit is not None:
            stmt = apply_pagination(stmt, limit=limit, offset=offset)
        result = await self._session.execute(stmt)
        return [
            scene_revision_model_to_record(revision, unit)
            for revision, unit in result.all()
        ]


def _pinned_by(dataset_version_id: str) -> tuple[Any, Any]:
    """Join target restricting Scene revisions to the ones a DatasetVersion's
    members pin."""
    return (
        DatasetVersionMemberModel,
        and_(
            DatasetVersionMemberModel.dataset_version_id == dataset_version_id,
            DatasetVersionMemberModel.scene_id == SceneRevisionModel.scene_id,
            DatasetVersionMemberModel.manifest_checksum
            == SceneRevisionModel.manifest_checksum,
        ),
    )


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

    async def latest_succeeded_for_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        run_type: RunType,
    ) -> dict[str, SceneRunRecord]:
        """For each Scene member of the DatasetVersion, its newest succeeded
        run of ``run_type`` that assessed exactly the revision the member
        pins. Runs for any other revision of the unit are ignored."""
        stmt = (
            select(SceneRunRecordModel)
            .join(
                DatasetVersionMemberModel,
                and_(
                    DatasetVersionMemberModel.scene_id == SceneRunRecordModel.scene_id,
                    DatasetVersionMemberModel.manifest_artifact_id
                    == SceneRunRecordModel.manifest_artifact_id,
                    DatasetVersionMemberModel.manifest_checksum
                    == SceneRunRecordModel.manifest_checksum,
                ),
            )
            .where(
                DatasetVersionMemberModel.dataset_version_id
                == make_dataset_version_id(dataset_id, dataset_version)
            )
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
