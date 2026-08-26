"""Unit tests for JobService execution_key dedup and retry-cap enforcement.

Uses tiny in-memory fakes implementing JobRepository/JobEventRepository
instead of mocks, since the logic under test (dedup lookup, retry counting)
is easiest to verify against real stored state rather than call assertions.
"""

from __future__ import annotations

import pytest

from app.platform.jobs.service import JobService
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobEvent,
    JobManifest,
    JobStatus,
    JobType,
)

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, JobManifest] = {}

    async def create(self, job: JobManifest) -> JobManifest:
        self.jobs[job.job_id] = job
        return job

    async def get(self, job_id: str) -> JobManifest | None:
        return self.jobs.get(job_id)

    async def update(self, job: JobManifest) -> JobManifest:
        self.jobs[job.job_id] = job
        return job

    async def list(self, **kwargs) -> list[JobManifest]:
        return list(self.jobs.values())

    async def count_by_status(self) -> dict[str, int]:
        return {}

    async def find_by_execution_key(
        self, execution_key: str, *, statuses: set[JobStatus]
    ) -> JobManifest | None:
        for job in self.jobs.values():
            if job.execution_key == execution_key and job.status in statuses:
                return job
        return None


class FakeJobEventRepository:
    def __init__(self) -> None:
        self.events: list[JobEvent] = []

    async def append(self, event: JobEvent) -> JobEvent:
        self.events.append(event)
        return event

    async def get(self, event_id: str) -> JobEvent | None:
        return next((e for e in self.events if e.event_id == event_id), None)

    async def list_for_job(self, job_id: str, **kwargs) -> list[JobEvent]:
        return [e for e in self.events if e.job_id == job_id]

    async def list_for_pipeline_run(
        self, pipeline_run_id: str, **kwargs
    ) -> list[JobEvent]:
        return [e for e in self.events if e.pipeline_run_id == pipeline_run_id]


def _service() -> tuple[JobService, FakeJobRepository]:
    repo = FakeJobRepository()
    service = JobService(
        repository=repo,
        event_repository=FakeJobEventRepository(),
        default_dataset_id=DATASET_ID,
        default_dataset_version=DATASET_VERSION,
    )
    return service, repo


def _request(**overrides) -> CreateJobRequest:
    base = dict(
        type=JobType.EXPORT_ANALYTICS_SNAPSHOT,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        params={},
    )
    base.update(overrides)
    return CreateJobRequest(**base)


async def test_identical_requests_return_same_job():
    service, _ = _service()

    first = await service.create_job(_request())
    second = await service.create_job(_request())

    assert first.job_id == second.job_id


async def test_force_bypasses_dedup():
    service, _ = _service()

    first = await service.create_job(_request())
    second = await service.create_job(_request(force=True))

    assert first.job_id != second.job_id


async def test_different_params_are_not_deduped():
    service, _ = _service()

    first = await service.create_job(_request(params={"tables": ["scenes"]}))
    second = await service.create_job(_request(params={"tables": ["samples"]}))

    assert first.job_id != second.job_id


async def test_failed_job_is_not_a_dedup_match():
    service, repo = _service()

    first = await service.create_job(_request())
    failed = first.model_copy(update={"status": JobStatus.FAILED})
    await repo.update(failed)

    second = await service.create_job(_request())

    assert first.job_id != second.job_id


async def test_mark_queued_increments_retry_count_on_failed_job():
    service, repo = _service()

    created = await service.create_job(_request(max_retries=2))
    failed = created.model_copy(update={"status": JobStatus.FAILED})
    await repo.update(failed)

    requeued = await service.mark_queued(created.job_id)

    assert requeued.status == JobStatus.QUEUED
    assert requeued.retry_count == 1


async def test_mark_queued_raises_when_retries_exhausted():
    service, repo = _service()

    created = await service.create_job(_request(max_retries=1))
    exhausted = created.model_copy(
        update={"status": JobStatus.FAILED, "retry_count": 1}
    )
    await repo.update(exhausted)

    with pytest.raises(ValueError, match="exhausted retries"):
        await service.mark_queued(created.job_id)


async def test_mark_queued_from_pending_does_not_touch_retry_count():
    service, _ = _service()

    created = await service.create_job(_request())
    assert created.status == JobStatus.PENDING

    queued = await service.mark_queued(created.job_id)

    assert queued.status == JobStatus.QUEUED
    assert queued.retry_count == 0
