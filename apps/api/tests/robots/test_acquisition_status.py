"""Derived acquisition status and operational report (ADR-008 §7, Phase 12.6).

Runs over the in-memory doubles of test_reconciliation.py. The store double
raises on any write and the facts double exposes reads only, so "nothing is
stored" is structural; the tests additionally compare the doubles before and
after and pin determinism for a fixed observation time.
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta

import pytest

from app.domains.robots.acquisition_status import (
    AcquisitionHealth as H,
    AcquisitionStage as Stage,
    acquisition_status_once,
    derive_health,
    summary_record,
)
from app.domains.robots.reconciliation import AcquisitionState
from app.domains.robots.reconciliation.model import RegistrationEvidence, RunReport
from sceneops_core.common.ids import robot_run_manifest_artifact_id
from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.robots.capture_scan import CaptureClass
from sceneops_core.robots.registration_failures import RegistrationFailureClass
from tests.robots.test_reconciliation import (
    RECORDING,
    ROOT,
    add_job,
    capture,
    capture_report,
    publish,
    register,
)
from tests.robots.test_artifact_lifecycle import NOW, T0

S = AcquisitionState


async def status_report(store, facts, *, now=NOW, **kwargs):
    return await acquisition_status_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        now=now,
        **kwargs,
    )


def status_of(report, run_id):
    return next(run for run in report.runs if run.run_id == run_id)


# ── health / stage table ──────────────────────────────────────────────────────


def test_every_state_has_a_health():
    """The table in ``derive.py`` covers the whole vocabulary: a new
    AcquisitionState cannot reach the status view without a decision."""
    for state in AcquisitionState:
        health, operator = derive_health(_run_with(state))
        assert isinstance(health, H) and isinstance(operator, bool)


def _run_with(state, reasons=()):
    return RunReport(
        run_id="r",
        state=state,
        reasons=tuple(reasons),
        capture=None,
        publication=None,
        publication_assessment=None,
        registration=RegistrationEvidence(
            robot_run=None,
            recording_artifact=None,
            manifest_artifact=None,
            execution_key=None,
            jobs=(),
            failed_job_count=0,
        ),
    )


@pytest.mark.parametrize(
    "state, reasons, health, operator",
    [
        (S.REGISTERED, (), H.OK, False),
        (S.CAPTURE_UNFINISHED, (), H.PENDING, False),
        (S.FINALIZED_NO_RECEIPT, (), H.PENDING, True),
        (S.PUBLISH_PENDING, (), H.PENDING, False),
        (
            S.PUBLICATION_INCOMPLETE,
            ("recording_without_manifest", "resumable_from_capture"),
            H.PENDING,
            False,
        ),
        (
            S.PUBLICATION_INCOMPLETE,
            ("recording_without_manifest", "no_capture_source"),
            H.PENDING,
            True,
        ),
        (S.PUBLICATION_INCOMPLETE, ("manifest_malformed",), H.INCONSISTENT, True),
        (
            S.PUBLICATION_INCOMPLETE,
            ("manifest_without_recording",),
            H.INCONSISTENT,
            True,
        ),
        (S.REGISTRATION_PENDING, ("no_registration_job",), H.PENDING, False),
        (S.REGISTRATION_PENDING, ("latest_job_cancelled",), H.PENDING, True),
        (
            S.REGISTRATION_PENDING,
            ("succeeded_job_without_robot_run", "latest_job_succeeded"),
            H.INCONSISTENT,
            True,
        ),
        (S.REGISTRATION_ACTIVE, ("job_running",), H.PENDING, False),
        (
            S.REGISTRATION_STALLED_CANDIDATE,
            ("job_inactive_beyond_threshold",),
            H.STALLED,
            False,
        ),
        (
            S.REGISTRATION_STALLED_CANDIDATE,
            ("attempt_budget_exhausted", "job_inactive_beyond_threshold"),
            H.STALLED,
            True,
        ),
        (S.REGISTRATION_FAILED_TRANSIENT, ("error_type:OSError",), H.PENDING, False),
        (S.REGISTRATION_FAILED_PERMANENT, (), H.FAILED, True),
        (S.PERMANENT_CONFLICT, ("manifest_checksum_differs",), H.INCONSISTENT, True),
        (S.INTEGRITY_INCIDENT, (), H.INCONSISTENT, True),
    ],
)
def test_health_and_operator_requirement_follow_the_state(
    state, reasons, health, operator
):
    assert derive_health(_run_with(state, reasons)) == (health, operator)


# ── one registered run: every fact comes from a durable source ────────────────


async def test_a_registered_run_reports_its_durable_facts(store, facts):
    register(facts, "run-1", publish(store, "run-1"))

    status = status_of(await status_report(store, facts), "run-1")

    assert (status.stage, status.health) == (Stage.REGISTERED, H.OK)
    assert status.classification == S.REGISTERED
    assert status.operator_required is False
    assert status.failure is None
    assert status.robot_id == "robot-001"
    assert status.recording_bytes == len(RECORDING)
    assert status.message_count == 3  # sum of the manifest's channel counts
    assert status.channel_count == 1
    assert status.timestamps.registered_at == T0
    assert status.timestamps.published_at == T0  # the store double's mtime
    assert status.timestamps.finalized_at is None  # no capture report supplied
    assert status.durations.publish_to_register_seconds == 0.0
    assert status.durations.finalize_to_register_seconds is None
    assert status.age_seconds == pytest.approx((NOW - T0).total_seconds())
    assert status.artifacts.referenced_objects == 2
    assert status.artifacts.referenced_bytes > len(RECORDING)
    assert status.findings == ()
    assert status.publication is not None
    assert status.publication.manifest_checksum is not None


async def test_the_capture_report_adds_the_capture_stage_and_durations(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    report = await status_report(
        store,
        facts,
        capture_report=capture_report(
            capture("run-1", CaptureClass.FINALIZED_WITH_RECEIPT)
        ),
    )

    status = status_of(report, "run-1")

    assert status.capture is not None
    assert status.capture.finalization_reason == "explicit_run_end"
    assert status.finalization_reason == "explicit_run_end"
    assert status.timestamps.finalized_at == T0
    assert status.durations.finalize_to_publish_seconds == 0.0
    assert status.durations.finalize_to_register_seconds == 0.0


async def test_capture_only_runs_are_visible_with_the_capture_report(store, facts):
    report = await status_report(
        store,
        facts,
        capture_report=capture_report(
            capture("unfinished", CaptureClass.CAPTURE_UNFINISHED),
            capture("final", CaptureClass.FINALIZED_WITH_RECEIPT),
            capture("legacy", CaptureClass.FINALIZED_NO_RECEIPT),
        ),
    )

    unfinished = status_of(report, "unfinished")
    assert (unfinished.stage, unfinished.health) == (
        Stage.CAPTURE_UNFINISHED,
        H.PENDING,
    )
    final = status_of(report, "final")
    assert (final.stage, final.health, final.operator_required) == (
        Stage.FINALIZED,
        H.PENDING,
        False,
    )
    # Known only from the receipt: no manifest exists yet.
    assert (final.recording_bytes, final.message_count) == (len(RECORDING), 3)
    legacy = status_of(report, "legacy")
    assert (legacy.stage, legacy.operator_required) == (Stage.FINALIZED, True)
    assert report.by_stage == {"capture_unfinished": 1, "finalized": 2}

    without = await status_report(store, facts)
    assert without.runs == () and without.runs_total == 0  # unobservable, not absent


# ── registration failure facts ────────────────────────────────────────────────


async def test_a_transient_failure_reports_its_attempts_and_stays_pending(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="OSError")

    status = status_of(await status_report(store, facts), "run-1")

    assert (status.stage, status.health) == (Stage.PUBLISHED, H.PENDING)
    assert status.failure is not None
    assert status.failure.failure_class == RegistrationFailureClass.TRANSIENT
    assert status.failure.error_type == "OSError"
    assert (status.failure.attempts, status.failure.attempts_remaining) == (1, 2)
    assert status.registration.latest_job is not None
    assert status.registration.latest_job.status == JobStatus.FAILED


async def test_a_registered_run_reports_no_failure_for_its_failed_history(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    add_job(facts, "run-1", JobStatus.FAILED, error_type="JobAbandoned", created_at=T0)
    add_job(facts, "run-1", JobStatus.SUCCEEDED, created_at=T0 + timedelta(minutes=1))

    status = status_of(await status_report(store, facts), "run-1")

    assert (status.health, status.failure) == (H.OK, None)
    # The attempts it took stay visible as registration facts.
    assert status.registration.failed_job_count == 1
    assert status.registration.abandoned_job_count == 1


async def test_a_spent_budget_is_failed_and_needs_an_operator(store, facts):
    publish(store, "run-1")
    for _ in range(3):
        add_job(facts, "run-1", JobStatus.FAILED, error_type="OSError")

    status = status_of(await status_report(store, facts), "run-1")

    assert (status.health, status.operator_required) == (H.FAILED, True)
    assert status.failure is not None
    assert status.failure.attempts == 3 and status.failure.attempts_remaining == 0
    assert status.registration.attempts_remaining == 0


async def test_a_permanent_failure_is_failed_with_its_class(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="RecordingVerificationError")

    status = status_of(await status_report(store, facts), "run-1")

    assert (status.health, status.operator_required) == (H.FAILED, True)
    assert status.failure.failure_class == RegistrationFailureClass.PERMANENT
    assert status.failure.error_type == "RecordingVerificationError"


async def test_an_inactive_job_is_stalled_and_an_abandoned_one_counts(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="JobAbandoned", created_at=T0)
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=T0 + timedelta(minutes=1))

    status = status_of(await status_report(store, facts), "run-1")

    assert (status.health, status.operator_required) == (H.STALLED, False)
    assert status.registration.abandoned_job_count == 1
    assert status.registration.failed_job_count == 1
    assert len(status.registration.active_job_ids) == 1


# ── artifact findings ─────────────────────────────────────────────────────────


async def test_an_artifact_incident_makes_the_run_inconsistent(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    # The registered run keeps its RobotRunRecord but loses its manifest's
    # ArtifactRecord: the manifest object is then unreferenced.
    del facts.artifacts[robot_run_manifest_artifact_id("run-1")]

    status = status_of(await status_report(store, facts), "run-1")

    assert status.health == H.INCONSISTENT
    assert status.operator_required is True
    assert status.findings  # the lifecycle entries that are not just referenced


async def test_orphan_candidates_and_pending_objects_appear_as_findings(store, facts):
    # O1: a recording with no manifest and, with a capture report, no capture.
    store.objects[f"{ROOT}/lonely/recording.mcap"] = RECORDING
    publish(store, "waiting")  # published, unregistered: pending (PN-2)

    report = await status_report(store, facts, capture_report=capture_report())

    lonely = status_of(report, "lonely")
    assert lonely.operator_required is True
    assert lonely.artifacts.orphan_candidate_objects == 1
    assert lonely.artifacts.orphan_candidate_bytes == len(RECORDING)
    (finding,) = lonely.findings
    assert finding.lifecycle_class.value == "orphan_candidate"
    assert finding.orphan_reason is not None and finding.risk is not None
    waiting = status_of(report, "waiting")
    assert waiting.artifacts.pending_objects == 2
    assert {f.lifecycle_class.value for f in waiting.findings} == {"pending"}


# ── the operational report ────────────────────────────────────────────────────


async def build_mixed(store, facts):
    register(facts, "ok-1", publish(store, "ok-1"))
    register(facts, "ok-2", publish(store, "ok-2"))
    publish(store, "pending-1")
    publish(store, "failed-1")
    for _ in range(3):
        add_job(facts, "failed-1", JobStatus.FAILED, error_type="OSError")
    publish(store, "retry-1")
    add_job(facts, "retry-1", JobStatus.FAILED, error_type="OSError", created_at=T0)
    publish(store, "conflict-1")
    register(facts, "conflict-1", "sha256:" + "0" * 64)
    publish(store, "stalled-1")
    add_job(facts, "stalled-1", JobStatus.RUNNING, created_at=T0)
    return capture_report(
        capture("capture-1", CaptureClass.CAPTURE_UNFINISHED),
        capture("final-1", CaptureClass.FINALIZED_WITH_RECEIPT),
    )


async def test_the_operational_report_aggregates_every_run(store, facts):
    capture_facts = await build_mixed(store, facts)

    report = await status_report(store, facts, capture_report=capture_facts)

    assert report.runs_total == 9
    assert report.by_classification == {
        "capture_unfinished": 1,
        "permanent_conflict": 1,
        "publish_pending": 1,
        "registered": 2,
        "registration_failed_permanent": 1,
        "registration_failed_transient": 1,
        "registration_pending": 1,
        "registration_stalled_candidate": 1,
    }
    assert report.by_health == {
        "failed": 1,
        "inconsistent": 1,
        "ok": 2,
        "pending": 4,
        "stalled": 1,
    }
    assert report.by_stage == {
        "capture_unfinished": 1,
        "finalized": 1,
        "published": 4,
        "registered": 3,
    }
    registration = report.registration
    assert (registration.pending, registration.active) == (1, 0)
    assert registration.stalled_candidates == 1
    assert (registration.failed_transient, registration.failed_permanent) == (1, 1)
    assert registration.budget_exhausted == 1
    assert registration.failed_jobs == 4  # 3 (failed-1) + 1 (retry-1)
    assert registration.attempts_used == {"0": 2, "1": 1, "3": 1}
    # The newest durable activity is the age basis; both are 30 days old here.
    assert set(report.oldest) == {
        "capture_unfinished",
        "publish_pending",
        "registration_failed_transient",
        "registration_pending",
        "registration_stalled_candidate",
    }
    assert report.oldest["registration_stalled_candidate"].run_id == "stalled-1"
    assert report.incidents.runs == 1
    assert report.incidents.by_reason == {"manifest_checksum_differs": 1}
    assert report.artifacts.referenced.count == 4
    assert report.artifacts.pending.count > 0
    # Every run but the capture-only one has a manifest (recording size known).
    assert report.recordings.runs_with_known_bytes == 8
    assert report.recordings.total_bytes == 8 * len(RECORDING)
    assert {item.run_id for item in report.attention} == {
        "failed-1",
        "conflict-1",
        "stalled-1",
    }
    assert report.operator_required == 2  # failed-1, conflict-1
    assert [run.run_id for run in report.runs] == sorted(
        run.run_id for run in report.runs
    )


async def test_recording_totals_count_only_runs_where_the_size_is_known(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    store.objects[f"{ROOT}/bare/recording.mcap"] = RECORDING  # no manifest, no receipt

    report = await status_report(store, facts)

    assert report.recordings.runs_with_known_bytes == 2  # the listing size counts
    assert report.recordings.total_bytes == 2 * len(RECORDING)
    assert report.recordings.runs_with_known_message_count == 1
    assert report.recordings.total_message_count == 3


async def test_summary_mode_drops_per_run_statuses_but_not_the_aggregates(store, facts):
    capture_facts = await build_mixed(store, facts)

    full = await status_report(store, facts, capture_report=capture_facts)
    summary = await status_report(
        store, facts, capture_report=capture_facts, include_runs=False
    )

    assert summary.runs == () and summary.runs_included is False
    assert full.runs_included is True
    assert summary.model_copy(update={"runs": full.runs, "runs_included": True}) == full
    record = summary_record(summary)
    json.dumps(record)  # a log record must be serializable
    assert record["runs"] == 9 and record["by_health"] == summary.by_health
    assert "runs_included" not in record and "findings" not in json.dumps(record)


# ── derived, never stored ─────────────────────────────────────────────────────


async def test_the_report_is_deterministic_for_fixed_facts_and_time(store, facts):
    capture_facts = await build_mixed(store, facts)

    first = await status_report(store, facts, capture_report=capture_facts)
    second = await status_report(store, facts, capture_report=capture_facts)

    assert json.dumps(first.to_json_dict(), sort_keys=True) == json.dumps(
        second.to_json_dict(), sort_keys=True
    )


async def test_observation_time_changes_only_ages_and_time_based_states(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    publish(store, "waiting")

    early = await status_report(store, facts, now=T0 + timedelta(hours=1))
    late = await status_report(store, facts, now=T0 + timedelta(days=2))

    assert status_of(early, "run-1").age_seconds == 3600.0
    assert status_of(late, "run-1").age_seconds == 2 * 86400.0
    assert status_of(early, "run-1").health == status_of(late, "run-1").health == H.OK
    assert early.observed_at != late.observed_at


async def test_nothing_is_written_or_stored(store, facts):
    capture_facts = await build_mixed(store, facts)
    objects_before = dict(store.objects)
    jobs_before = copy.deepcopy(facts.jobs)
    runs_before = copy.deepcopy(facts.runs)
    artifacts_before = copy.deepcopy(facts.artifacts)

    await status_report(store, facts, capture_report=capture_facts)

    assert store.objects == objects_before  # MemoryStore raises on any write
    assert facts.jobs == jobs_before
    assert facts.runs == runs_before
    assert facts.artifacts == artifacts_before


async def test_a_naive_observation_time_is_rejected(store, facts):
    with pytest.raises(ValueError, match="timezone-aware"):
        await status_report(store, facts, now=T0.replace(tzinfo=None))
