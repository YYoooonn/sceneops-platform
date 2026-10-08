"""Unit tests for JobService execution_key dedup and retry-cap enforcement.

Uses tiny in-memory fakes implementing JobRepository/JobEventRepository
instead of mocks, since the logic under test (dedup lookup, retry counting)
is easiest to verify against real stored state rather than call assertions.
"""

from __future__ import annotations

import pytest

from sceneops_execution.jobs.service import JobDispatchConflictError, JobService
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobEvent,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_db.repositories.jobs import JobExecutionKeyInFlightError

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

    async def queue_if_unchanged(self, job: JobManifest) -> JobManifest | None:
        stored = self.jobs.get(job.job_id)
        if (
            stored is None
            or stored.status != job.status
            or stored.retry_count != job.retry_count
        ):
            return None
        retry = 1 if stored.status == JobStatus.FAILED else 0
        queued = stored.model_copy(
            update={
                "status": JobStatus.QUEUED,
                "retry_count": stored.retry_count + retry,
            }
        )
        self.jobs[job.job_id] = queued
        return queued

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


class _LosingJobRepository(FakeJobRepository):
    """Its first insert loses to a concurrent request, as the in-flight unique
    index makes a real one: the winner appears and the insert is refused."""

    async def create(self, job: JobManifest) -> JobManifest:
        if not self.jobs:
            winner = job.model_copy(update={"job_id": "job-winner"})
            self.jobs[winner.job_id] = winner
            raise JobExecutionKeyInFlightError(job.execution_key)
        return await super().create(job)


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


class FakeArtifactRepository:
    """No ALIGN_EPISODE requests are made in this file -- see
    test_job_service_align_episode.py for the ALIGN_EPISODE-specific
    pre-execution-key source resolution (SceneOps V2 Request 2.3A). This
    fake only needs to satisfy JobService's constructor."""

    async def create(self, **kwargs):
        raise NotImplementedError

    async def get(self, artifact_id: str):
        return None

    async def list(self, **kwargs) -> list:
        return []


def _service() -> tuple[JobService, FakeJobRepository]:
    repo = FakeJobRepository()
    service = JobService(
        repository=repo,
        event_repository=FakeJobEventRepository(),
        artifact_repository=FakeArtifactRepository(),
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


async def test_force_creates_a_new_job_after_a_succeeded_one():
    service, repo = _service()

    first = await service.create_job(_request())
    await repo.update(first.model_copy(update={"status": JobStatus.SUCCEEDED}))
    second = await service.create_job(_request(force=True))

    assert first.job_id != second.job_id


@pytest.mark.parametrize(
    "status", [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]
)
async def test_force_returns_the_job_still_in_flight(status):
    service, repo = _service()

    first = await service.create_job(_request())
    await repo.update(first.model_copy(update={"status": status}))
    forced = await service.create_job(_request(force=True))

    assert forced.job_id == first.job_id
    assert len(repo.jobs) == 1


async def test_a_lost_insert_returns_the_concurrent_winner():
    """The in-flight unique index refused the insert: the Job a concurrent request
    committed after this one's lookup is returned, and no CREATED event is
    written for the refused one."""
    repo = _LosingJobRepository()
    events = FakeJobEventRepository()
    service = JobService(
        repository=repo,
        event_repository=events,
        artifact_repository=FakeArtifactRepository(),
    )

    job = await service.create_job(_request())

    assert job.job_id == "job-winner"
    assert events.events == []


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


async def test_mark_queued_refuses_a_job_that_changed_after_it_was_read():
    """The write is conditional on the state the checks judged: a Job that a
    worker claimed between the read and the write is not queued."""
    service, repo = _service()
    created = await service.create_job(_request())

    class _ClaimedAfterRead(FakeJobRepository):
        async def get(self, job_id):
            read = await super().get(job_id)
            self.jobs[job_id] = read.model_copy(
                update={"status": JobStatus.RUNNING, "worker_id": "worker-1"}
            )
            return read

    raced = _ClaimedAfterRead()
    raced.jobs = repo.jobs
    service = JobService(
        repository=raced,
        event_repository=FakeJobEventRepository(),
        artifact_repository=FakeArtifactRepository(),
    )

    with pytest.raises(JobDispatchConflictError):
        await service.mark_queued(created.job_id)

    assert repo.jobs[created.job_id].status == JobStatus.RUNNING
    assert repo.jobs[created.job_id].worker_id == "worker-1"


async def test_mark_queued_refuses_a_job_not_in_the_state_the_caller_decided_on():
    service, repo = _service()
    created = await service.create_job(_request())
    await repo.update(created.model_copy(update={"status": JobStatus.QUEUED}))

    with pytest.raises(JobDispatchConflictError):
        await service.mark_queued(created.job_id, expected_status=JobStatus.PENDING)

    assert repo.jobs[created.job_id].status == JobStatus.QUEUED
