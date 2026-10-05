"""Read-only acquisition reconciliation (ADR-008 §5.1, Phase 12.3): store
facts + capture report + PostgreSQL facts -> one classified state per run.

Everything below runs over in-memory doubles. The store double raises on any
write and the facts double exposes reads only, so "reconciliation mutates
nothing" is asserted structurally as well as by comparing state before/after.
The real PostgreSQL + MinIO vertical is test_reconciliation_integration.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.domains.robots.reconciliation import (
    AcquisitionState,
    ClassificationPolicy,
    ReconciliationReport,
    reconcile_once,
    register_robot_run_execution_key,
)
from app.domains.robots.registration import RobotRunRegistrationService  # noqa: F401
from app.platform.jobs.service import JobService
from sceneops_core.artifacts.contracts import ArtifactObject
from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import CreateJobRequest, JobManifest, JobStatus, JobType
from sceneops_core.robots.capture_receipt import FinalizationReason
from sceneops_core.robots.capture_scan import (
    CaptureClass,
    CaptureObservation,
    CaptureReceiptFacts,
    CaptureScanReport,
)
from sceneops_core.robots.manifest import (
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    ChannelFact,
    RecordingFormat,
    RecordingRef,
    RobotRunManifest,
)
from sceneops_core.robots.schemas import RobotRunRecord

ROOT = "s3://bucket/robot_runs"
RECORDING = b"mcap-bytes-" * 10
_T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
S = AcquisitionState


# ── doubles ───────────────────────────────────────────────────────────────────


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def list_objects(self, uri):
        prefix = uri.rstrip("/") + "/"
        return sorted(
            (
                ArtifactObject(uri=k, size_bytes=len(v), last_modified=_T0)
                for k, v in self.objects.items()
                if k.startswith(prefix)
            ),
            key=lambda item: item.uri,
        )

    async def read_bytes(self, uri):
        return self.objects[uri]

    async def write_bytes(self, uri, data):
        raise AssertionError(f"reconciliation wrote {uri}")

    async def write_json(self, uri, payload):
        raise AssertionError(f"reconciliation wrote {uri}")

    async def delete_prefix(self, uri):
        raise AssertionError(f"reconciliation deleted {uri}")


class FakeFacts:
    """The PostgreSQL facts, reads only; nothing here can write."""

    def __init__(self) -> None:
        self.runs: dict[str, RobotRunRecord] = {}
        self.artifacts: dict[str, ArtifactRecord] = {}
        self.jobs: list[JobManifest] = []
        self.entered = 0

    async def robot_runs(self, run_ids):
        return {r: self.runs[r] for r in run_ids if r in self.runs}

    async def artifact_records(self, artifact_ids):
        return {a: self.artifacts[a] for a in artifact_ids if a in self.artifacts}

    async def register_jobs(self, execution_keys):
        keys = set(execution_keys)
        return sorted(
            (j for j in self.jobs if j.execution_key in keys),
            key=lambda j: (j.created_at, j.job_id),
        )

    def scope(self):
        @asynccontextmanager
        async def _scope():
            self.entered += 1
            yield self

        return _scope


@pytest.fixture()
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture()
def facts() -> FakeFacts:
    return FakeFacts()


# ── fixture builders ──────────────────────────────────────────────────────────


def _manifest(run_id: str, **overrides) -> RobotRunManifest:
    values = dict(
        run_id=run_id,
        robot_id="robot-001",
        robot_platform=None,
        started_at=datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 5, 11, 1, tzinfo=UTC),
        recording=RecordingRef(
            format=RecordingFormat.MCAP,
            uri=f"{ROOT}/{run_id}/recording.mcap",
            checksum=sha256_checksum(RECORDING),
            size_bytes=len(RECORDING),
        ),
        capture=CaptureInfo(
            source=CaptureSource(kind=CaptureSourceKind.FILE),
            source_clock="mcap_log_time",
        ),
        channels=[
            ChannelFact(
                topic="/odom",
                message_encoding="cdr",
                schema_name="nav_msgs/msg/Odometry",
                schema_encoding="ros2msg",
                message_count=3,
            )
        ],
    )
    values.update(overrides)
    return RobotRunManifest(**values)


def publish(store: MemoryStore, run_id: str, **manifest_overrides) -> str:
    """Write recording + manifest; returns the manifest checksum."""
    data = _manifest(run_id, **manifest_overrides).to_canonical_bytes()
    store.objects[f"{ROOT}/{run_id}/recording.mcap"] = RECORDING
    store.objects[f"{ROOT}/{run_id}/robot_run_manifest.json"] = data
    return sha256_checksum(data)


def manifest_uri(run_id: str) -> str:
    return f"{ROOT}/{run_id}/robot_run_manifest.json"


def register(facts: FakeFacts, run_id: str, manifest_checksum: str, **record) -> None:
    """The committed result of a registration: RobotRunRecord + its two
    ArtifactRecords."""
    facts.runs[run_id] = RobotRunRecord(
        run_id=run_id,
        robot_id="robot-001",
        started_at=_T0,
        ended_at=_T0,
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=robot_run_recording_artifact_id(run_id),
        manifest_artifact_id=robot_run_manifest_artifact_id(run_id),
        manifest_checksum=record.get("manifest_checksum", manifest_checksum),
        registered_at=_T0,
    )
    facts.artifacts[robot_run_recording_artifact_id(run_id)] = ArtifactRecord(
        artifact_id=robot_run_recording_artifact_id(run_id),
        kind="robot_run_recording",
        uri=f"{ROOT}/{run_id}/recording.mcap",
        size_bytes=len(RECORDING),
        checksum=sha256_checksum(RECORDING),
    )
    facts.artifacts[robot_run_manifest_artifact_id(run_id)] = ArtifactRecord(
        artifact_id=robot_run_manifest_artifact_id(run_id),
        kind="robot_run_manifest",
        uri=manifest_uri(run_id),
        checksum=manifest_checksum,
    )


def add_job(
    facts: FakeFacts,
    run_id: str,
    status: JobStatus,
    *,
    created_at: datetime = _T0,
    error_type: str | None = None,
    heartbeat_at: datetime | None = None,
    started_at: datetime | None = None,
) -> JobManifest:
    job = JobManifest(
        job_id=f"job-{len(facts.jobs):04d}",
        type=JobType.REGISTER_ROBOT_RUN,
        status=status,
        params={"manifest_uri": manifest_uri(run_id)},
        execution_key=register_robot_run_execution_key(manifest_uri(run_id)),
        error=ErrorInfo(type=error_type, message="boom") if error_type else None,
        created_at=created_at,
        queued_at=created_at,
        started_at=started_at,
        heartbeat_at=heartbeat_at,
    )
    facts.jobs.append(job)
    return job


def capture(
    run_id: str,
    classification: CaptureClass,
    *,
    checksum: str | None = None,
    robot_id: str = "robot-001",
    problem: str | None = None,
) -> CaptureObservation:
    receipt = None
    if classification == CaptureClass.FINALIZED_WITH_RECEIPT:
        receipt = CaptureReceiptFacts(
            robot_id=robot_id,
            robot_platform=None,
            recording_file=f"{run_id}_0.mcap",
            recording_checksum=checksum or sha256_checksum(RECORDING),
            recording_size_bytes=len(RECORDING),
            message_count=3,
            finalization_reason=FinalizationReason.EXPLICIT_RUN_END.value,
            finalized_at=_T0,
        )
    return CaptureObservation(
        run_id=run_id,
        classification=classification,
        partial_present=classification == CaptureClass.CAPTURE_UNFINISHED,
        final_present=classification != CaptureClass.CAPTURE_UNFINISHED,
        mcap_files=(),
        receipt=receipt,
        receipt_problem=problem,
        modified_at=_T0,
    )


def capture_report(*observations: CaptureObservation) -> CaptureScanReport:
    return CaptureScanReport(runs=tuple(observations), unrecognized_entries=())


async def reconcile(store, facts, **kwargs) -> ReconciliationReport:
    return await reconcile_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        **kwargs,
    )


def state_of(report: ReconciliationReport, run_id: str):
    run = next(run for run in report.runs if run.run_id == run_id)
    return run.state, run.reasons


# ── publication states ────────────────────────────────────────────────────────


async def test_published_run_with_no_job_is_registration_pending(store, facts):
    publish(store, "run-1")

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.REGISTRATION_PENDING,
        ("no_registration_job",),
    )
    assert report.counts == {"registration_pending": 1}
    assert report.capture_observed is False


async def test_recording_without_manifest_is_publication_incomplete(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.PUBLICATION_INCOMPLETE,
        ("recording_without_manifest",),
    )
    # No manifest, so nothing could have been submitted.
    assert report.runs[0].registration.execution_key is None


async def test_manifest_without_recording_is_publication_incomplete(store, facts):
    publish(store, "run-1")
    del store.objects[f"{ROOT}/run-1/recording.mcap"]

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.PUBLICATION_INCOMPLETE,
        ("manifest_without_recording",),
    )


async def test_malformed_manifest_is_publication_incomplete_not_registerable(
    store, facts
):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    store.objects[f"{ROOT}/run-1/robot_run_manifest.json"] = b"{truncated"

    report = await reconcile(store, facts)

    state, reasons = state_of(report, "run-1")
    assert state == S.PUBLICATION_INCOMPLETE
    assert reasons[0] == "manifest_malformed" or reasons[1] == "manifest_malformed"
    assert any(r.startswith("RobotRunManifestError") for r in reasons)


async def test_tampered_recording_of_an_unregistered_run_is_an_integrity_incident(
    store, facts
):
    publish(store, "run-size")
    store.objects[f"{ROOT}/run-size/recording.mcap"] = RECORDING + b"x"
    publish(store, "run-bytes")
    store.objects[f"{ROOT}/run-bytes/recording.mcap"] = b"Z" * len(RECORDING)

    report = await reconcile(store, facts)

    assert state_of(report, "run-size") == (
        S.INTEGRITY_INCIDENT,
        ("recording_size_mismatch",),
    )
    assert state_of(report, "run-bytes") == (
        S.INTEGRITY_INCIDENT,
        ("recording_checksum_mismatch",),
    )


async def test_byte_check_can_be_skipped_and_then_only_the_listing_is_trusted(
    store, facts
):
    publish(store, "run-bytes")
    store.objects[f"{ROOT}/run-bytes/recording.mcap"] = b"Z" * len(RECORDING)

    report = await reconcile(store, facts, verify_unregistered_recordings=False)

    assert state_of(report, "run-bytes")[0] == S.REGISTRATION_PENDING


# ── registration correlation ──────────────────────────────────────────────────


async def test_matching_record_is_registered_whatever_any_job_says(store, facts):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    # Worker killed after commit (W9) and a failed retry: both are noise.
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=_T0)
    add_job(
        facts,
        "run-1",
        JobStatus.FAILED,
        created_at=_T0 + timedelta(hours=1),
        error_type="ConnectionError",
    )

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (S.REGISTERED, ())
    assert report.runs[0].registration.failed_job_count == 1


async def test_registered_run_recording_is_not_rehashed(store, facts):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    # Same size, different bytes: registration verified the original and a
    # registered run is not re-read; only the listing size is compared.
    store.objects[f"{ROOT}/run-1/recording.mcap"] = b"Z" * len(RECORDING)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (S.REGISTERED, ())
    assert report.runs[0].publication.recording_check.value == "not_checked"


async def test_job_success_alone_does_not_mean_registered(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.SUCCEEDED)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.REGISTRATION_PENDING,
        ("latest_job_succeeded", "succeeded_job_without_robot_run"),
    )


async def test_manifest_checksum_differing_from_the_record_is_a_permanent_conflict(
    store, facts
):
    publish(store, "run-1")
    register(facts, "run-1", "sha256:" + "0" * 64)
    # Even a job in flight or failed does not soften it.
    add_job(facts, "run-1", JobStatus.RUNNING)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.PERMANENT_CONFLICT,
        ("manifest_checksum_differs",),
    )


@pytest.mark.parametrize(
    "status", [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]
)
async def test_in_flight_job_is_registration_active(store, facts, status):
    publish(store, "run-1")
    add_job(facts, "run-1", status)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.REGISTRATION_ACTIVE,
        (f"job_{status.value}",),
    )


@pytest.mark.parametrize(
    ("error_type", "state"),
    [
        ("ConnectionError", S.REGISTRATION_FAILED_TRANSIENT),
        ("ArtifactReadError", S.REGISTRATION_FAILED_TRANSIENT),
        ("SomethingNobodyClassified", S.REGISTRATION_FAILED_TRANSIENT),
        (None, S.REGISTRATION_FAILED_TRANSIENT),
        ("RecordingVerificationError", S.REGISTRATION_FAILED_PERMANENT),
        ("RobotPlatformConflictError", S.REGISTRATION_FAILED_PERMANENT),
        ("PublishedArtifactMissingError", S.REGISTRATION_FAILED_PERMANENT),
        ("NonCanonicalRobotRunManifestError", S.REGISTRATION_FAILED_PERMANENT),
    ],
)
async def test_failed_job_is_classified_by_error_type(store, facts, error_type, state):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type=error_type)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        state,
        (f"error_type:{error_type or 'unknown'}",),
    )
    assert report.runs[0].registration.failed_job_count == 1


async def test_newest_job_decides_and_every_attempt_is_counted(store, facts):
    publish(store, "run-1")
    for hour in range(3):
        add_job(
            facts,
            "run-1",
            JobStatus.FAILED,
            created_at=_T0 + timedelta(hours=hour),
            error_type="OSError",
        )

    report = await reconcile(store, facts)

    registration = report.runs[0].registration
    assert state_of(report, "run-1")[0] == S.REGISTRATION_FAILED_TRANSIENT
    assert registration.failed_job_count == 3
    assert [j.job_id for j in registration.jobs] == ["job-0000", "job-0001", "job-0002"]
    assert registration.execution_key == register_robot_run_execution_key(
        manifest_uri("run-1")
    )

    add_job(facts, "run-1", JobStatus.RUNNING, created_at=_T0 + timedelta(hours=4))
    report = await reconcile(store, facts)
    assert state_of(report, "run-1")[0] == S.REGISTRATION_ACTIVE


async def test_jobs_correlate_by_execution_key_only(store, facts):
    publish(store, "run-1")
    publish(store, "run-2")
    add_job(facts, "run-2", JobStatus.RUNNING)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1")[0] == S.REGISTRATION_PENDING
    assert state_of(report, "run-2")[0] == S.REGISTRATION_ACTIVE
    assert report.counts == {"registration_active": 1, "registration_pending": 1}


async def test_cancelled_job_leaves_registration_pending(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.CANCELLED)

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.REGISTRATION_PENDING,
        ("latest_job_cancelled",),
    )


# ── stall candidate: a seam, not a decision ───────────────────────────────────


async def test_no_threshold_means_no_run_is_ever_a_stall_candidate(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=_T0 - timedelta(days=365))

    report = await reconcile(store, facts)

    assert state_of(report, "run-1")[0] == S.REGISTRATION_ACTIVE


async def test_threshold_promotes_only_jobs_inactive_beyond_it(store, facts):
    publish(store, "run-old")
    add_job(
        facts,
        "run-old",
        JobStatus.RUNNING,
        created_at=_T0,
        started_at=_T0,
        heartbeat_at=_T0 + timedelta(minutes=1),
    )
    publish(store, "run-recent")
    add_job(
        facts,
        "run-recent",
        JobStatus.RUNNING,
        created_at=_T0,
        heartbeat_at=_T0 + timedelta(minutes=50),
    )
    publish(store, "run-mixed")
    add_job(facts, "run-mixed", JobStatus.QUEUED, created_at=_T0)
    add_job(
        facts, "run-mixed", JobStatus.QUEUED, created_at=_T0 + timedelta(minutes=58)
    )

    report = await reconcile(
        store,
        facts,
        policy=ClassificationPolicy(stall_candidate_after=timedelta(minutes=30)),
        now=_T0 + timedelta(hours=1),
    )

    assert state_of(report, "run-old") == (
        S.REGISTRATION_STALLED_CANDIDATE,
        ("job_inactive_beyond_threshold",),
    )
    assert state_of(report, "run-recent")[0] == S.REGISTRATION_ACTIVE
    assert state_of(report, "run-mixed")[0] == S.REGISTRATION_ACTIVE


async def test_stall_candidate_never_overrides_a_matching_record(store, facts):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=_T0)

    report = await reconcile(
        store,
        facts,
        policy=ClassificationPolicy(stall_candidate_after=timedelta(seconds=1)),
        now=_T0 + timedelta(days=1),
    )

    assert state_of(report, "run-1") == (S.REGISTERED, ())


# ── integrity incidents on the registration side ──────────────────────────────


async def test_artifact_records_without_a_robot_run_are_an_incident(store, facts):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    del facts.runs["run-1"]

    report = await reconcile(store, facts)

    assert state_of(report, "run-1") == (
        S.INTEGRITY_INCIDENT,
        ("artifact_record_without_robot_run",),
    )


@pytest.mark.parametrize(
    ("break_it", "reason"),
    [
        (
            lambda s, f: s.objects.pop(f"{ROOT}/run-1/recording.mcap"),
            "registered_recording_object_missing",
        ),
        (
            lambda s, f: s.objects.__setitem__(
                f"{ROOT}/run-1/recording.mcap", RECORDING + b"x"
            ),
            "recording_size_mismatch",
        ),
        (
            lambda s, f: f.artifacts.pop(robot_run_manifest_artifact_id("run-1")),
            "registered_artifact_record_missing",
        ),
        (
            lambda s, f: f.artifacts.pop(robot_run_recording_artifact_id("run-1")),
            "registered_artifact_record_missing",
        ),
        (
            lambda s, f: f.artifacts.__setitem__(
                robot_run_recording_artifact_id("run-1"),
                f.artifacts[robot_run_recording_artifact_id("run-1")].model_copy(
                    update={"checksum": "sha256:" + "9" * 64}
                ),
            ),
            "artifact_record_recording_mismatch",
        ),
        (
            lambda s, f: f.artifacts.__setitem__(
                robot_run_manifest_artifact_id("run-1"),
                f.artifacts[robot_run_manifest_artifact_id("run-1")].model_copy(
                    update={"checksum": "sha256:" + "9" * 64}
                ),
            ),
            "artifact_record_manifest_checksum_mismatch",
        ),
        (
            lambda s, f: f.artifacts.__setitem__(
                robot_run_recording_artifact_id("run-1"),
                f.artifacts[robot_run_recording_artifact_id("run-1")].model_copy(
                    update={"uri": "s3://elsewhere/recording.mcap"}
                ),
            ),
            "artifact_record_recording_uri_mismatch",
        ),
    ],
    ids=[
        "recording_object_gone",
        "recording_resized",
        "manifest_artifact_missing",
        "recording_artifact_missing",
        "recording_artifact_checksum",
        "manifest_artifact_checksum",
        "recording_artifact_uri",
    ],
)
async def test_registered_run_contradicted_by_its_facts_is_an_incident(
    store, facts, break_it, reason
):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    break_it(store, facts)

    report = await reconcile(store, facts)

    state, reasons = state_of(report, "run-1")
    assert state == S.INTEGRITY_INCIDENT
    assert reason in reasons


async def test_registered_run_whose_manifest_object_is_gone_or_malformed(store, facts):
    checksum = publish(store, "run-gone")
    register(facts, "run-gone", checksum)
    del store.objects[manifest_uri("run-gone")]
    checksum = publish(store, "run-bad")
    register(facts, "run-bad", checksum)
    store.objects[manifest_uri("run-bad")] = b"garbage"

    report = await reconcile(
        store,
        facts,
        capture_report=capture_report(
            capture("run-gone", CaptureClass.FINALIZED_WITH_RECEIPT)
        ),
    )

    assert state_of(report, "run-gone") == (
        S.INTEGRITY_INCIDENT,
        ("registered_manifest_missing",),
    )
    assert state_of(report, "run-bad") == (
        S.INTEGRITY_INCIDENT,
        ("registered_manifest_malformed",),
    )


# ── capture discovery ─────────────────────────────────────────────────────────


async def test_capture_only_runs_map_to_capture_states(store, facts):
    report = await reconcile(
        store,
        facts,
        capture_report=capture_report(
            capture("run-live", CaptureClass.CAPTURE_UNFINISHED),
            capture("run-ready", CaptureClass.FINALIZED_WITH_RECEIPT),
            capture("run-legacy", CaptureClass.FINALIZED_NO_RECEIPT),
            capture(
                "run-bad",
                CaptureClass.FINALIZED_RECEIPT_INVALID,
                problem="CaptureReceiptError: nope",
            ),
        ),
    )

    assert report.capture_observed is True
    assert {r.run_id: r.state for r in report.runs} == {
        "run-bad": S.INTEGRITY_INCIDENT,
        "run-legacy": S.FINALIZED_NO_RECEIPT,
        "run-live": S.CAPTURE_UNFINISHED,
        "run-ready": S.PUBLISH_PENDING,
    }
    assert state_of(report, "run-bad")[1] == ("capture_receipt_invalid",)
    assert report.runs[0].publication is None
    assert report.counts == {
        "capture_unfinished": 1,
        "finalized_no_receipt": 1,
        "integrity_incident": 1,
        "publish_pending": 1,
    }


async def test_incomplete_publication_says_where_it_can_be_resumed_from(store, facts):
    for run_id in ("run-resumable", "run-legacy", "run-orphan", "run-live"):
        store.objects[f"{ROOT}/{run_id}/recording.mcap"] = RECORDING

    report = await reconcile(
        store,
        facts,
        capture_report=capture_report(
            capture("run-resumable", CaptureClass.FINALIZED_WITH_RECEIPT),
            capture("run-legacy", CaptureClass.FINALIZED_NO_RECEIPT),
            capture("run-live", CaptureClass.CAPTURE_UNFINISHED),
        ),
    )

    assert state_of(report, "run-resumable") == (
        S.PUBLICATION_INCOMPLETE,
        ("recording_without_manifest", "resumable_from_capture"),
    )
    assert state_of(report, "run-legacy")[1] == (
        "capture_without_receipt",
        "recording_without_manifest",
    )
    assert state_of(report, "run-live")[1] == (
        "capture_unfinished",
        "recording_without_manifest",
    )
    # O1: the recording may be the only copy.
    assert state_of(report, "run-orphan")[1] == (
        "no_capture_source",
        "recording_without_manifest",
    )


async def test_without_a_capture_report_capture_facts_are_not_invented(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING

    report = await reconcile(store, facts)

    assert state_of(report, "run-1")[1] == ("recording_without_manifest",)
    assert report.runs[0].capture is None


async def test_capture_receipt_contradicting_the_manifest_is_an_incident(store, facts):
    publish(store, "run-bytes")
    publish(store, "run-robot")

    report = await reconcile(
        store,
        facts,
        capture_report=capture_report(
            capture(
                "run-bytes",
                CaptureClass.FINALIZED_WITH_RECEIPT,
                checksum="sha256:" + "5" * 64,
            ),
            capture(
                "run-robot", CaptureClass.FINALIZED_WITH_RECEIPT, robot_id="other-robot"
            ),
        ),
    )

    assert state_of(report, "run-bytes") == (
        S.INTEGRITY_INCIDENT,
        ("capture_receipt_recording_mismatch",),
    )
    assert state_of(report, "run-robot") == (
        S.INTEGRITY_INCIDENT,
        ("capture_receipt_robot_mismatch",),
    )


async def test_publication_supersedes_a_finalized_capture(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.RUNNING)

    report = await reconcile(
        store,
        facts,
        capture_report=capture_report(
            capture("run-1", CaptureClass.FINALIZED_WITH_RECEIPT)
        ),
    )

    run = report.runs[0]
    assert run.state == S.REGISTRATION_ACTIVE
    assert run.capture.classification == CaptureClass.FINALIZED_WITH_RECEIPT
    assert run.publication is not None


async def test_unrecognized_objects_and_capture_entries_are_reported(store, facts):
    publish(store, "run-1")
    store.objects[f"{ROOT}/loose.json"] = b"{}"

    report = await reconcile(
        store,
        facts,
        capture_report=CaptureScanReport(runs=(), unrecognized_entries=("junk",)),
    )

    assert [u.item.uri for u in report.unrecognized_objects] == [f"{ROOT}/loose.json"]
    assert report.capture_unrecognized_entries == ("junk",)
    assert [r.run_id for r in report.runs] == ["run-1"]


# ── purity: determinism and zero mutation ─────────────────────────────────────


def _durable_state(store: MemoryStore, facts: FakeFacts):
    return (
        dict(store.objects),
        dict(facts.runs),
        dict(facts.artifacts),
        list(facts.jobs),
    )


async def test_reconciliation_is_deterministic_and_changes_nothing(store, facts):
    checksum = publish(store, "run-registered")
    register(facts, "run-registered", checksum)
    publish(store, "run-pending")
    publish(store, "run-failed")
    add_job(facts, "run-failed", JobStatus.FAILED, error_type="OSError")
    publish(store, "run-active")
    add_job(facts, "run-active", JobStatus.RUNNING)
    store.objects[f"{ROOT}/run-orphan/recording.mcap"] = RECORDING
    captures = capture_report(capture("run-ready", CaptureClass.FINALIZED_WITH_RECEIPT))
    before = _durable_state(store, facts)

    first = await reconcile(store, facts, capture_report=captures)
    second = await reconcile(store, facts, capture_report=captures)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert _durable_state(store, facts) == before  # MemoryStore raises on any write
    assert [r.run_id for r in first.runs] == sorted(r.run_id for r in first.runs)
    assert first.counts == {
        "publication_incomplete": 1,
        "publish_pending": 1,
        "registered": 1,
        "registration_active": 1,
        "registration_failed_transient": 1,
        "registration_pending": 1,
    }
    assert facts.entered == 2
    assert ReconciliationReport.model_validate(first.to_json_dict()) == first


async def test_report_contains_no_wall_clock_reading(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.RUNNING)

    first = await reconcile(store, facts)
    second = await reconcile(
        store,
        facts,
        policy=ClassificationPolicy(stall_candidate_after=timedelta(days=36500)),
    )

    # Reading the clock for a threshold must not leak it into the report.
    assert first == second


async def test_a_run_needs_some_observed_fact():
    from app.domains.robots.reconciliation import classify_run
    from app.domains.robots.reconciliation.model import RegistrationEvidence

    with pytest.raises(ValueError):
        classify_run(
            capture=None,
            capture_observed=False,
            publication=None,
            assessment=None,
            registration=RegistrationEvidence(
                robot_run=None,
                recording_artifact=None,
                manifest_artifact=None,
                execution_key=None,
                jobs=(),
                failed_job_count=0,
            ),
        )


# ── execution key pin ─────────────────────────────────────────────────────────


async def test_execution_key_matches_the_one_job_submission_computes():
    """Correlation is by execution key; if submission and reconciliation ever
    computed different keys, every Job would silently stop correlating."""

    class _Repo:
        def __init__(self) -> None:
            self.jobs: list[JobManifest] = []

        async def create(self, job):
            self.jobs.append(job)
            return job

        async def find_by_execution_key(self, key, *, statuses):
            return None

    class _Events:
        async def append(self, event):
            return event

    repo = _Repo()
    service = JobService(
        repository=repo, event_repository=_Events(), artifact_repository=object()
    )
    uri = "s3://b/robot_runs/run-9/robot_run_manifest.json"

    submitted = await service.create_job(
        CreateJobRequest(type=JobType.REGISTER_ROBOT_RUN, params={"manifest_uri": uri})
    )
    forced = await service.create_job(
        CreateJobRequest(
            type=JobType.REGISTER_ROBOT_RUN, params={"manifest_uri": uri}, force=True
        )
    )

    assert submitted.execution_key == register_robot_run_execution_key(uri)
    # A forced replacement shares the logical identity (ADR-008 §5.2).
    assert forced.execution_key == submitted.execution_key
    assert register_robot_run_execution_key(uri) != register_robot_run_execution_key(
        uri.replace("run-9", "run-8")
    )
