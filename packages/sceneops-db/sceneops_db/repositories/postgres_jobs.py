from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.jobs import JobManifest, JobStatus
from sceneops_core.time import utc_now_iso
from sceneops_db.models.job import JobModel


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
            stmt = stmt.where(JobModel.status == self._enum_to_str(status))

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
        model = await self.session.get(JobModel, manifest.jobId)

        if model is None:
            raise FileNotFoundError(f"Job not found: {manifest.jobId}")

        updated = self._to_model(manifest)

        model.type = updated.type
        model.status = updated.status
        model.dataset_id = updated.dataset_id
        model.dataset_version = updated.dataset_version
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
        manifest_data["status"] = self._enum_to_str(status)
        manifest_data["updatedAt"] = utc_now_iso()

        if error is not None:
            manifest_data["error"] = error

        if result is not None:
            manifest_data["result"] = result

        updated_manifest = JobManifest.model_validate(manifest_data)
        return await self.update(updated_manifest)

    def _to_model(self, manifest: JobManifest) -> JobModel:
        data = manifest.model_dump(mode="json")

        params = data.get("params") or {}
        result = data.get("result")
        error = data.get("error")

        return JobModel(
            id=data["jobId"],
            type=self._enum_to_str(data["type"]),
            status=self._enum_to_str(data["status"]),
            dataset_id=data.get("datasetId"),
            dataset_version=data.get("datasetVersion"),
            run_id=self._extract_run_id(result),
            evaluation_id=self._extract_evaluation_id(result),
            params=params if isinstance(params, dict) else {},
            result=result if isinstance(result, dict) else None,
            error=error,
            retry_count=int(data.get("retryCount") or 0),
            max_retries=int(data.get("maxRetries") or 0),
            worker_id=data.get("workerId"),
            queued_at=self._extract_datetime(data.get("queuedAt")),
            locked_at=self._extract_datetime(data.get("lockedAt")),
            heartbeat_at=self._extract_datetime(data.get("heartbeatAt")),
            manifest=data,
            started_at=self._extract_datetime(data.get("startedAt")),
            finished_at=self._extract_datetime(data.get("finishedAt")),
        )

    def _to_schema(self, model: JobModel) -> JobManifest:
        return JobManifest.model_validate(model.manifest)

    def _extract_run_id(self, result: Any) -> str | None:
        if not isinstance(result, dict):
            return None

        value = result.get("runId") or result.get("run_id") or result.get("inferenceRunId")
        return str(value) if value is not None else None

    def _extract_evaluation_id(self, result: Any) -> str | None:
        if not isinstance(result, dict):
            return None

        value = (
            result.get("evaluationId")
            or result.get("evaluation_id")
            or result.get("evaluationRunId")
        )
        return str(value) if value is not None else None

    def _extract_datetime(self, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value

        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)

        return None

    def _enum_to_str(self, value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)
