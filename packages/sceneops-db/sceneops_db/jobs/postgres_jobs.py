from __future__ import annotations

from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import JobManifest, JobStatus

from sceneops_core.time import utc_now
from sceneops_db.jobs import JobModel
from sceneops_db.utils import extract_datetime, to_error_json, enum_to_str


class PostgresJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, manifest: JobManifest) -> JobManifest:
        model = self._to_model(manifest)

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(self, job_id: str) -> JobManifest:
        model = await self.session.get(JobModel, job_id)

        if model is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        return self._to_schema(model)

    async def list(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[JobManifest]:
        stmt = select(JobModel)

        if status is not None:
            stmt = stmt.where(JobModel.status == enum_to_str(status))

        if job_type is not None:
            stmt = stmt.where(JobModel.type == job_type)

        if dataset_id is not None:
            stmt = stmt.where(JobModel.dataset_id == dataset_id)

        if dataset_version is not None:
            stmt = stmt.where(JobModel.dataset_version == dataset_version)

        stmt = stmt.order_by(JobModel.created_at.desc())

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update(self, manifest: JobManifest) -> JobManifest:
        model = await self.session.get(JobModel, manifest.job_id)

        if model is None:
            raise FileNotFoundError(f"Job not found: {manifest.job_id}")

        updated = self._to_model(manifest)

        model.type = updated.type
        model.status = updated.status
        model.dataset_id = updated.dataset_id
        model.dataset_version = updated.dataset_version
        model.pipeline_run_id = updated.pipeline_run_id
        model.pipeline_step_run_id = updated.pipeline_step_run_id
        model.pipeline_step_name = updated.pipeline_step_name
        model.run_id = updated.run_id
        model.evaluation_id = updated.evaluation_id
        model.params = updated.params
        model.result = updated.result
        model.error = updated.error
        model.retry_count = updated.retry_count
        model.max_retries = updated.max_retries
        model.worker_id = updated.worker_id
        model.queued_at = updated.queued_at
        model.locked_at = updated.locked_at
        model.heartbeat_at = updated.heartbeat_at
        model.manifest = updated.manifest
        model.started_at = updated.started_at
        model.finished_at = updated.finished_at

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def count_by_status(self) -> dict[str, int]:
        # pylint: disable=not-callable
        stmt = select(JobModel.status, func.count(JobModel.id)).group_by(
            JobModel.status
        )

        result = await self.session.execute(stmt)

        return {str(status): count for status, count in result.all()}

    async def list_recent_failures(
        self,
        *,
        limit: int = 10,
    ) -> list[JobManifest]:
        stmt = (
            select(JobModel)
            .where(JobModel.status == enum_to_str(JobStatus.FAILED))
            .order_by(JobModel.updated_at.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> JobManifest:
        model = await self.session.get(JobModel, job_id)

        if model is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        manifest_data = dict(model.manifest)
        manifest_data["status"] = enum_to_str(status)
        manifest_data["updated_at"] = utc_now()

        if error is not None:
            manifest_data["error"] = to_error_json(error)

        if result is not None:
            manifest_data["result"] = result

        updated_manifest = JobManifest.model_validate(manifest_data)
        return await self.update(updated_manifest)

    def _to_model(self, manifest: JobManifest) -> JobModel:
        manifest_data = manifest.to_db_dict()

        result = manifest.result if isinstance(manifest.result, dict) else None
        params = manifest.params if isinstance(manifest.params, dict) else {}

        return JobModel(
            id=manifest.job_id,
            type=enum_to_str(manifest.type),
            status=enum_to_str(manifest.status),
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            pipeline_run_id=manifest.pipeline_run_id,
            pipeline_step_run_id=manifest.pipeline_step_run_id,
            pipeline_step_name=manifest.pipeline_step_name,
            run_id=self._extract_run_id(result),
            evaluation_id=self._extract_evaluation_id(result),
            params=params,
            result=result,
            error=to_error_json(manifest.error),
            retry_count=manifest.retry_count,
            max_retries=manifest.max_retries,
            worker_id=manifest.worker_id,
            queued_at=extract_datetime(manifest.queued_at),
            locked_at=extract_datetime(manifest.locked_at),
            heartbeat_at=extract_datetime(manifest.heartbeat_at),
            manifest=manifest_data,
            started_at=extract_datetime(manifest.started_at),
            finished_at=extract_datetime(manifest.finished_at),
        )

    def _to_schema(self, model: JobModel) -> JobManifest:
        return JobManifest.model_validate(
            {
                "job_id": model.id,
                "type": model.type,
                "status": model.status,
                "dataset_id": model.dataset_id,
                "dataset_version": model.dataset_version,
                "pipeline_run_id": model.pipeline_run_id,
                "pipeline_step_run_id": model.pipeline_step_run_id,
                "pipeline_step_name": model.pipeline_step_name,
                "params": model.params or {},
                "result": model.result,
                "error": to_error_json(model.error),
                "retry_count": model.retry_count,
                "max_retries": model.max_retries,
                "worker_id": model.worker_id,
                "queued_at": model.queued_at,
                "locked_at": model.locked_at,
                "heartbeat_at": model.heartbeat_at,
                "started_at": model.started_at,
                "finished_at": model.finished_at,
                "created_at": model.created_at,
                "updated_at": model.updated_at,
                "steps": (model.manifest or {}).get("steps", []),
            }
        )

    def _extract_run_id(self, result: JsonDict | None) -> str | None:
        if not result:
            return None

        value = result.get("inference_run_id") or result.get("run_id")
        return str(value) if value else None

    def _extract_evaluation_id(self, result: JsonDict | None) -> str | None:
        if not result:
            return None

        value = result.get("evaluation_run_id") or result.get("evaluation_id")
        return str(value) if value else None
