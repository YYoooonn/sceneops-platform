"""POST /robot-runs:register submits REGISTER_ROBOT_RUN through the normal
Job mechanism (create -> commit -> dispatch) and holds no registration
logic itself. Service-level tests with in-memory fakes, matching this test
suite's no-TestClient convention, plus a route-table check that the bare
metadata-only ``POST /robot-runs`` no longer exists."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.domains.robots.registration import RobotRunRegistrationService
from app.platform.jobs.service import JobService
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType


class _FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, JobManifest] = {}

    async def create(self, job: JobManifest) -> JobManifest:
        self.jobs[job.job_id] = job
        return job

    async def find_by_execution_key(self, execution_key, *, statuses):
        for job in self.jobs.values():
            if job.execution_key == execution_key and job.status in statuses:
                return job
        return None


class _FakeEventRepository:
    async def append(self, event):
        return event


class _Session:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def commit(self) -> None:
        self._log.append("commit")


class _FakeDispatchFacade:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def dispatch(self, job_id: str) -> ExecutionDispatchResult:
        self._log.append(f"dispatch:{job_id}")
        return ExecutionDispatchResult(
            execution_id="exec-1",
            external_id="exec-1",
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
        )


@pytest.fixture()
def harness():
    log: list[str] = []
    repository = _FakeJobRepository()

    @asynccontextmanager
    async def _session_factory():
        yield _Session(log)

    def _job_service(_session) -> JobService:
        return JobService(
            repository=repository,
            event_repository=_FakeEventRepository(),
            artifact_repository=object(),
        )

    service = RobotRunRegistrationService(
        session_factory=_session_factory,
        job_service_factory=_job_service,
        dispatch_facade=_FakeDispatchFacade(log),
    )
    return service, repository, log


async def test_creates_commits_then_dispatches_register_job(harness) -> None:
    service, repository, log = harness

    response = await service.submit("s3://b/robot_runs/run-1/robot_run_manifest.json")

    job = response.job
    assert job.type == JobType.REGISTER_ROBOT_RUN
    assert job.params["manifest_uri"] == (
        "s3://b/robot_runs/run-1/robot_run_manifest.json"
    )
    assert repository.jobs == {job.job_id: job}
    # Job creation commits before dispatch so a worker never claims an
    # uncommitted job.
    assert log == ["commit", f"dispatch:{job.job_id}"]
    assert response.execution is not None
    assert response.execution.resource_id == job.job_id


async def test_existing_equivalent_job_is_not_redispatched(harness) -> None:
    service, repository, log = harness
    first = await service.submit("s3://b/m.json")
    repository.jobs[first.job.job_id] = first.job.model_copy(
        update={"status": JobStatus.SUCCEEDED}
    )
    log.clear()

    second = await service.submit("s3://b/m.json")

    assert second.job.job_id == first.job.job_id
    assert second.execution is None
    assert log == ["commit"]


async def test_identical_retry_returns_original_execution_result(harness) -> None:
    """An identical POST /robot-runs:register is answered by Job dedup: the
    already-succeeded Job comes back with its original registrar result.
    ``created`` describes the execution that produced that result, so it
    stays True -- no second registrar execution happens, and nothing
    rewrites the old result. A registrar that actually runs again reports
    created=False (apps/worker/tests/robots/test_registration_integration.py)."""
    service, repository, log = harness
    first = await service.submit("s3://b/m.json")
    original_result = {
        "run_id": "run-1",
        "robot_id": "robot-1",
        "recording_artifact_id": "art-robotrun-run-1",
        "manifest_artifact_id": "art-robotrunmanifest-run-1",
        "manifest_checksum": "sha256:" + "1" * 64,
        "created": True,
    }
    repository.jobs[first.job.job_id] = first.job.model_copy(
        update={"status": JobStatus.SUCCEEDED, "result": original_result}
    )
    log.clear()

    retry = await service.submit("s3://b/m.json")

    assert list(repository.jobs) == [first.job.job_id]
    assert retry.job.job_id == first.job.job_id
    assert retry.job.status == JobStatus.SUCCEEDED
    assert retry.job.result == original_result
    assert retry.execution is None
    assert log == ["commit"]


async def test_empty_manifest_uri_rejected(harness) -> None:
    service, _, _ = harness
    with pytest.raises(ValueError):
        await service.submit("")


def test_route_table_has_register_and_no_bare_create() -> None:
    from app.main import app

    robot_run_routes = {
        (method, route.path)
        for route in app.routes
        if "/robot-runs" in getattr(route, "path", "")
        for method in route.methods
    }
    assert ("POST", "/api/v1/robot-runs:register") in robot_run_routes
    assert ("POST", "/api/v1/robot-runs") not in robot_run_routes
    assert ("GET", "/api/v1/robot-runs/{run_id}") in robot_run_routes
