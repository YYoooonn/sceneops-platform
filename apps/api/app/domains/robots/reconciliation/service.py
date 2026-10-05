"""Stateless, read-only acquisition reconciliation (ADR-008 §3.2, §8 step 12.3).

``reconcile_once`` is the whole contract: read the durable facts, classify,
return the report. It keeps no state between invocations, so every call is a
complete reconciliation, safe at any frequency and concurrently (L-3). It runs
inside any process that has an ArtifactStore and a database -- it needs no HTTP
server, no Celery and no Redis (ADR-008 §3.2).

Facts, in the order they are read:

1. ArtifactStore listing of the RobotRun root, plus each manifest object
   (``sceneops_core.robots.published_scan``).
2. PostgreSQL, in one ``READ ONLY`` transaction: RobotRunRecords, the two
   deterministic RobotRun ArtifactRecords per run, and every
   ``REGISTER_ROBOT_RUN`` Job whose execution key matches a published manifest.
3. For runs that are published but not registered, the recording bytes
   (size + sha256 against the manifest), because registration would reject a
   mismatch and the report should say so first. Registered runs are not
   re-hashed: registration verified them and their ArtifactRecords are
   compared to the manifest instead.
4. Optionally a capture report (``CaptureScanReport``) produced next to the
   capture volume; the platform never mounts that volume.

Nothing is written anywhere: no object, no row, no Job, no event (L-2, L-10).
The database transaction is declared read-only so that is enforced by
PostgreSQL, not only by this code. Acting on the report is 12.4.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.executions import compute_execution_key, params_for_execution_key
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType, parse_job_params
from sceneops_core.robots.capture_scan import CaptureScanReport
from sceneops_core.robots.published_scan import (
    PublicationAssessment,
    PublishedRunObservation,
    classify_publication,
    observe_published_runs,
    verify_recording_bytes,
)
from sceneops_core.robots.registration_failures import classify_registration_failure
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.postgres.robots import PostgresRobotRunRepository

from .classify import ClassificationPolicy, classify_run
from .model import (
    ArtifactRecordFacts,
    JobFacts,
    ReconciliationReport,
    RegistrationEvidence,
    RobotRunFacts,
    RunReport,
)


def register_robot_run_execution_key(manifest_uri: str) -> str:
    """The execution key ``JobService.create_job`` gives
    ``REGISTER_ROBOT_RUN(manifest_uri)``: the logical identity of one
    registration attempt across every Job row created for it (ADR-008 §5.2).
    Built from the same three functions ``create_job`` uses; a test pins the
    two together."""
    params = parse_job_params(
        JobType.REGISTER_ROBOT_RUN, {"manifest_uri": manifest_uri}
    ).model_dump()
    return compute_execution_key(
        kind="job",
        type=JobType.REGISTER_ROBOT_RUN.value,
        dataset_id=None,
        dataset_version=None,
        params=params_for_execution_key(JobType.REGISTER_ROBOT_RUN, params),
    )


class RegistrationFactSource(Protocol):
    """The PostgreSQL facts reconciliation reads. Read-only by construction."""

    async def robot_runs(self, run_ids: Sequence[str]) -> dict[str, RobotRunRecord]: ...

    async def artifact_records(
        self, artifact_ids: Sequence[str]
    ) -> dict[str, ArtifactRecord]: ...

    async def register_jobs(
        self, execution_keys: Sequence[str]
    ) -> list[JobManifest]: ...


RegistrationFactsScope = Callable[
    [], AbstractAsyncContextManager[RegistrationFactSource]
]


class PostgresRegistrationFacts:
    def __init__(self, session: AsyncSession) -> None:
        self._runs = PostgresRobotRunRepository(session)
        self._artifacts = PostgresArtifactRefRepository(session)
        self._jobs = PostgresJobRepository(session)

    async def robot_runs(self, run_ids: Sequence[str]) -> dict[str, RobotRunRecord]:
        return await self._runs.get_many(run_ids)

    async def artifact_records(
        self, artifact_ids: Sequence[str]
    ) -> dict[str, ArtifactRecord]:
        return await self._artifacts.get_many(list(artifact_ids))

    async def register_jobs(self, execution_keys: Sequence[str]) -> list[JobManifest]:
        return await self._jobs.list_for_execution_keys(
            execution_keys, type=JobType.REGISTER_ROBOT_RUN
        )


def _artifact_facts(record: ArtifactRecord | None) -> ArtifactRecordFacts | None:
    if record is None:
        return None
    return ArtifactRecordFacts(
        artifact_id=record.artifact_id,
        uri=record.uri,
        size_bytes=record.size_bytes,
        checksum=record.checksum,
    )


def _job_facts(job: JobManifest) -> JobFacts:
    error_type = job.error.type if job.error is not None else None
    return JobFacts(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        queued_at=job.queued_at,
        started_at=job.started_at,
        heartbeat_at=job.heartbeat_at,
        finished_at=job.finished_at,
        error_type=error_type,
        failure_class=(
            classify_registration_failure(error_type)
            if job.status == JobStatus.FAILED
            else None
        ),
    )


def postgres_registration_facts(
    session_factory: async_sessionmaker[AsyncSession],
) -> RegistrationFactsScope:
    """A facts scope backed by one short ``READ ONLY`` PostgreSQL transaction
    per entry. It is rolled back on exit and never committed."""

    @asynccontextmanager
    async def _scope() -> AsyncIterator[RegistrationFactSource]:
        async with session_factory() as session:
            try:
                # Must be the transaction's first statement: from here on
                # PostgreSQL rejects any write instead of trusting this module.
                await session.execute(text("SET TRANSACTION READ ONLY"))
                yield PostgresRegistrationFacts(session)
            finally:
                await session.rollback()

    return _scope


async def reconcile_once(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    registration_facts: RegistrationFactsScope,
    capture_report: CaptureScanReport | None = None,
    policy: ClassificationPolicy | None = None,
    now: datetime | None = None,
    verify_unregistered_recordings: bool = True,
) -> ReconciliationReport:
    """Observe the durable acquisition facts and classify every ``run_id``.

    ``now`` is read only when ``policy`` carries an age threshold; the report
    itself never contains it. ``verify_unregistered_recordings=False`` skips the
    recording byte comparison (listing sizes are still compared)."""
    policy = policy or ClassificationPolicy()
    if policy.stall_candidate_after is not None and now is None:
        now = datetime.now(UTC)

    scan = await observe_published_runs(artifact_store, root_uri)
    published = {observation.run_id: observation for observation in scan.runs}
    captured = (
        {observation.run_id: observation for observation in capture_report.runs}
        if capture_report is not None
        else {}
    )
    run_ids = sorted(set(published) | set(captured))

    execution_keys = {
        run_id: register_robot_run_execution_key(observation.manifest_object.uri)
        for run_id, observation in published.items()
        if observation.manifest_object is not None
    }
    artifact_ids = [
        make_id(run_id)
        for run_id in run_ids
        for make_id in (
            robot_run_recording_artifact_id,
            robot_run_manifest_artifact_id,
        )
    ]
    async with registration_facts() as facts:
        records = await facts.robot_runs(run_ids)
        artifacts = await facts.artifact_records(artifact_ids)
        jobs = await facts.register_jobs(sorted(set(execution_keys.values())))
    jobs_by_key: dict[str, list[JobManifest]] = {}
    for job in jobs:
        if job.execution_key is not None:
            jobs_by_key.setdefault(job.execution_key, []).append(job)

    run_reports: list[RunReport] = []
    for run_id in run_ids:
        observation: PublishedRunObservation | None = published.get(run_id)
        if (
            observation is not None
            and verify_unregistered_recordings
            and run_id not in records
        ):
            observation = await verify_recording_bytes(artifact_store, observation)
        assessment: PublicationAssessment | None = (
            classify_publication(observation) if observation is not None else None
        )

        execution_key = execution_keys.get(run_id)
        run_jobs = (
            [_job_facts(job) for job in jobs_by_key.get(execution_key, [])]
            if execution_key is not None
            else []
        )
        record = records.get(run_id)
        registration = RegistrationEvidence(
            robot_run=(
                RobotRunFacts(
                    robot_id=record.robot_id,
                    manifest_checksum=record.manifest_checksum,
                    recording_artifact_id=record.recording_artifact_id,
                    manifest_artifact_id=record.manifest_artifact_id,
                    registered_at=record.registered_at,
                )
                if record is not None
                else None
            ),
            recording_artifact=_artifact_facts(
                artifacts.get(robot_run_recording_artifact_id(run_id))
            ),
            manifest_artifact=_artifact_facts(
                artifacts.get(robot_run_manifest_artifact_id(run_id))
            ),
            execution_key=execution_key,
            jobs=tuple(run_jobs),
            failed_job_count=sum(
                1 for job in run_jobs if job.status == JobStatus.FAILED
            ),
        )

        capture = captured.get(run_id)
        state, reasons = classify_run(
            capture=capture,
            capture_observed=capture_report is not None,
            publication=observation,
            assessment=assessment,
            registration=registration,
            policy=policy,
            now=now,
        )
        run_reports.append(
            RunReport(
                run_id=run_id,
                state=state,
                reasons=reasons,
                capture=capture,
                publication=observation,
                publication_assessment=assessment,
                registration=registration,
            )
        )

    counts = Counter(report.state.value for report in run_reports)
    return ReconciliationReport(
        root_uri=root_uri,
        capture_observed=capture_report is not None,
        runs=tuple(run_reports),
        unrecognized_objects=scan.unrecognized,
        capture_unrecognized_entries=(
            capture_report.unrecognized_entries if capture_report is not None else ()
        ),
        counts=dict(sorted(counts.items())),
    )


__all__ = [
    "PostgresRegistrationFacts",
    "RegistrationFactSource",
    "RegistrationFactsScope",
    "postgres_registration_facts",
    "reconcile_once",
    "register_robot_run_execution_key",
]
