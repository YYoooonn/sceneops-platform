"""Concurrent ``JobService.create_job`` calls with one execution key, against real
PostgreSQL: requests for the same idempotent Job converge on one Job.

Every caller is one API request: its own session and transaction, committed before
it answers (``app.core.dependencies.get_db_session``). The tests commit their own
uniquely-keyed Jobs into the disposable database of `make test-integration`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.domains.robots.reconciliation import register_robot_run_execution_key
from app.platform.jobs.service import JobService
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobEventType,
    JobStatus,
    JobType,
)
from sceneops_db.models.jobs import JobEventModel
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.repositories.jobs import JobExecutionKeyInFlightError
from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)

CALLERS = 8


@pytest_asyncio.fixture(autouse=True)
async def _fresh_database_connection():
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set -- run via `make test-integration` "
            "against a running `make local-up` stack."
        )

    reset_async_engine_cache()
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"Postgres not reachable at SCENEOPS_DATABASE_URL: {exc}")

    yield

    await dispose_async_engine()


class _GatedJobRepository(PostgresJobRepository):
    """Holds a caller after its first execution-key lookup until every caller has
    looked: all of them pass the existence check before any of them inserts, the
    widest check-then-insert window, opened deterministically."""

    def __init__(self, session, gate: asyncio.Barrier) -> None:
        super().__init__(session)
        self._gate: asyncio.Barrier | None = gate

    async def find_by_execution_key(self, execution_key, *, statuses):
        found = await super().find_by_execution_key(execution_key, statuses=statuses)
        if self._gate is not None:
            gate, self._gate = self._gate, None
            await gate.wait()
        return found


@dataclass(frozen=True)
class _Observation:
    returned_job_ids: list[str]
    errors: list[str]
    stored_job_ids: list[str]
    created_events: int


def _manifest_uri() -> str:
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    return f"s3://sceneops-test/robot_runs/{run_id}/robot_run_manifest.json"


def _request(manifest_uri: str, *, force: bool = False) -> CreateJobRequest:
    return CreateJobRequest(
        type=JobType.REGISTER_ROBOT_RUN,
        params={"manifest_uri": manifest_uri},
        force=force,
    )


async def _create_in_own_transaction(
    request: CreateJobRequest, gate: asyncio.Barrier | None
) -> str:
    async with get_async_sessionmaker()() as session:
        repository = (
            PostgresJobRepository(session)
            if gate is None
            else _GatedJobRepository(session, gate)
        )
        job = await JobService(
            repository=repository,
            event_repository=PostgresJobEventRepository(session),
            artifact_repository=PostgresArtifactRefRepository(session),
        ).create_job(request)
        await session.commit()
        return job.job_id


async def _race(manifest_uri: str, *, gated: bool, force: bool = False) -> _Observation:
    request = _request(manifest_uri, force=force)
    gate = asyncio.Barrier(CALLERS) if gated else None
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(_create_in_own_transaction(request, gate) for _ in range(CALLERS)),
            return_exceptions=True,
        ),
        timeout=30,
    )

    async with get_async_sessionmaker()() as session:
        stored = await PostgresJobRepository(session).list_for_execution_keys(
            [register_robot_run_execution_key(manifest_uri)],
            type=JobType.REGISTER_ROBOT_RUN,
        )
        stored_ids = [job.job_id for job in stored]
        created_events = await session.scalar(
            select(func.count())
            .select_from(JobEventModel)
            .where(JobEventModel.job_id.in_(stored_ids or [""]))
            .where(JobEventModel.type == JobEventType.CREATED.value)
        )

    return _Observation(
        returned_job_ids=[o for o in outcomes if isinstance(o, str)],
        errors=[repr(o) for o in outcomes if isinstance(o, BaseException)],
        stored_job_ids=stored_ids,
        created_events=created_events or 0,
    )


def _assert_converged(observed: _Observation) -> None:
    assert not observed.errors, observed
    assert len(observed.stored_job_ids) == 1, observed
    assert set(observed.returned_job_ids) == set(observed.stored_job_ids), observed
    assert observed.created_events == 1, observed


async def test_callers_that_all_pass_the_existence_check_converge_on_one_job():
    """Every caller finds no Job for the key before any of them inserts: only the
    database can still tell them apart."""
    _assert_converged(await _race(_manifest_uri(), gated=True))


async def test_ungated_concurrent_callers_converge_on_one_job():
    _assert_converged(await _race(_manifest_uri(), gated=False))


async def test_forced_callers_after_a_succeeded_job_converge_on_one_new_job():
    """force skips the succeeded Job, not the in-flight one another forced caller
    just created."""
    manifest_uri = _manifest_uri()
    succeeded = await _create_in_own_transaction(_request(manifest_uri), None)
    async with get_async_sessionmaker()() as session:
        repository = PostgresJobRepository(session)
        job = await repository.get(succeeded)
        await repository.update(job.model_copy(update={"status": JobStatus.SUCCEEDED}))
        await session.commit()

    observed = await _race(manifest_uri, gated=True, force=True)

    assert not observed.errors, observed
    new = [job_id for job_id in observed.stored_job_ids if job_id != succeeded]
    assert len(new) == 1, observed
    assert set(observed.returned_job_ids) == set(new), observed
    assert observed.created_events == 2, observed


async def test_a_failed_job_is_not_retried_beside_an_in_flight_one():
    """Retrying a FAILED Job would put a second Job with its key in flight."""
    manifest_uri = _manifest_uri()
    async with get_async_sessionmaker()() as session:
        repository = PostgresJobRepository(session)
        service = JobService(
            repository=repository,
            event_repository=PostgresJobEventRepository(session),
            artifact_repository=PostgresArtifactRefRepository(session),
        )
        first = await service.create_job(
            _request(manifest_uri).model_copy(update={"max_retries": 1})
        )
        await repository.update(first.model_copy(update={"status": JobStatus.FAILED}))
        replacement = await service.create_job(_request(manifest_uri))
        await session.commit()
    assert replacement.job_id != first.job_id

    async with get_async_sessionmaker()() as session:
        service = JobService(
            repository=PostgresJobRepository(session),
            event_repository=PostgresJobEventRepository(session),
            artifact_repository=PostgresArtifactRefRepository(session),
        )
        with pytest.raises(JobExecutionKeyInFlightError):
            await service.mark_queued(first.job_id)

    async with get_async_sessionmaker()() as session:
        stored = await PostgresJobRepository(session).get(first.job_id)
    assert stored.status == JobStatus.FAILED
