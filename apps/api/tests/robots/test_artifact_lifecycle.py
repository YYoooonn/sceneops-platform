"""Read-only artifact lifecycle classification (ADR-008 §6, Phase 12.5):
store listing + ArtifactRecords (the §6.1 reference query) + the reconciler's
per-run state -> one class per object.

Runs over the in-memory doubles of test_reconciliation.py. The store double
raises on any write or delete and the facts double exposes reads only, so "read
only" is structural; tests additionally compare state before and after. The real
PostgreSQL + MinIO vertical is
apps/worker/tests/robots/test_artifact_lifecycle_vertical_integration.py.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from app.config import ApiSettings
from app.domains.robots.artifact_lifecycle import (
    ArtifactLifecyclePolicy,
    ArtifactLifecycleReport,
    EntrySubject,
    LifecycleEntry,
    artifact_lifecycle_once,
)
from app.domains.robots.reconciliation import AcquisitionState, reconcile_once
from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.robots.artifact_lifecycle import (
    ORPHAN_RISK,
    LifecycleClass,
    OrphanReason,
    RiskTier,
)
from sceneops_core.robots.capture_scan import CaptureClass
from sceneops_core.robots.registration_failures import REGISTRATION_ATTEMPT_BUDGET
from tests.robots.test_reconciliation import (
    RECORDING,
    ROOT,
    FakeFacts,
    MemoryStore,
    add_job,
    capture,
    capture_report,
    manifest_uri,
    publish,
    register,
)

C = LifecycleClass
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
# Objects default to a modification time of 2026-10-05 12:00Z; observing a month
# later makes them far older than both graces. ``young`` re-stamps them.
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
NOW = T0 + 30 * DAY


async def lifecycle(
    store: MemoryStore, facts: FakeFacts, *, now: datetime = NOW, **kwargs
) -> ArtifactLifecycleReport:
    return await artifact_lifecycle_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        now=now,
        **kwargs,
    )


def restamp(store: MemoryStore, run_id: str, modified: datetime) -> None:
    for name in ("recording.mcap", "robot_run_manifest.json"):
        store.modified[f"{ROOT}/{run_id}/{name}"] = modified


def of(report: ArtifactLifecycleReport, run_id: str) -> dict[str, LifecycleEntry]:
    """The entries of one run by role (objects) or subject (everything else)."""
    return {
        (e.role.value if e.role else e.subject.value): e
        for e in report.entries
        if e.run_id == run_id
    }


def classes(report: ArtifactLifecycleReport, run_id: str) -> dict[str, C]:
    return {key: e.lifecycle_class for key, e in of(report, run_id).items()}


# ── referenced ────────────────────────────────────────────────────────────────


async def test_registered_run_objects_are_referenced(store, facts):
    register(facts, "run-1", publish(store, "run-1"))

    report = await lifecycle(store, facts)

    assert classes(report, "run-1") == {
        "recording": C.REFERENCED,
        "manifest": C.REFERENCED,
    }
    recording = of(report, "run-1")["recording"]
    assert recording.artifact_ids == (robot_run_recording_artifact_id("run-1"),)
    assert recording.reasons == ()
    assert recording.acquisition_state == "registered"
    assert report.summary.referenced.count == 2
    assert report.summary.referenced.bytes == sum(e.size_bytes for e in report.entries)
    assert report.summary.pending.count == 0
    assert report.summary.orphan_candidates.count == 0
    assert report.summary.integrity_incidents.count == 0
    assert report.unconfirmed_findings == 0


async def test_stale_job_next_to_a_matching_record_is_still_referenced(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=T0)  # killed worker (W9)

    report = await lifecycle(store, facts)

    assert set(classes(report, "run-1").values()) == {C.REFERENCED}


async def test_a_referenced_object_is_never_a_candidate_at_any_age(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    for age in (timedelta(0), HOUR, 8 * DAY, 3650 * DAY):
        report = await lifecycle(store, facts, now=T0 + age)
        assert set(classes(report, "run-1").values()) == {C.REFERENCED}


async def test_reference_is_by_uri_not_by_the_run_prefix_name(store, facts):
    # An ArtifactRecord with an arbitrary id that carries exactly the object's
    # URI references it, whatever the object is called.
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    facts.artifacts["someone-elses-id"] = ArtifactRecord(
        artifact_id="someone-elses-id",
        kind="robot_run_recording",
        uri=f"{ROOT}/run-1/recording.mcap",
        size_bytes=len(RECORDING),
        checksum=sha256_checksum(RECORDING),
    )

    report = await lifecycle(store, facts)

    entry = of(report, "run-1")["recording"]
    assert entry.lifecycle_class == C.REFERENCED
    assert entry.artifact_ids == ("someone-elses-id",)


# ── pending: PN-1 / PN-2 / PN-3 / PN-4 ────────────────────────────────────────


@pytest.mark.parametrize("age", [HOUR, 6 * DAY, 40 * DAY, 3650 * DAY])
async def test_published_unregistered_pair_is_pending_at_any_age(store, facts, age):
    publish(store, "run-1")  # no Job, no record: registration_pending (PN-2)

    report = await lifecycle(store, facts, now=T0 + age)

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.PENDING}
    assert all(e.reasons == ("pn2_registration_unfinished",) for e in entries.values())
    assert all(e.orphan_reason is None for e in entries.values())
    assert report.summary.orphan_candidates.count == 0


@pytest.mark.parametrize(
    "scenario, expected_reasons",
    [
        ("active", ("pn2_registration_unfinished", "pn4_active_registration_job")),
        ("stalled", ("pn2_registration_unfinished", "pn4_active_registration_job")),
        (
            "queued_replacement",
            ("pn2_registration_unfinished", "pn4_active_registration_job"),
        ),
        ("transient_budget_left", ("pn2_registration_unfinished",)),
        ("succeeded_without_record", ("pn2_registration_unfinished",)),
        ("cancelled", ("pn2_registration_unfinished",)),
    ],
)
async def test_registration_work_in_flight_protects_the_pair(
    store, facts, scenario, expected_reasons
):
    publish(store, "run-1")
    if scenario == "active":
        add_job(
            facts,
            "run-1",
            JobStatus.RUNNING,
            created_at=NOW - 60 * timedelta(seconds=1),
        )
    elif scenario == "stalled":
        add_job(facts, "run-1", JobStatus.RUNNING, created_at=T0)  # 30 days inactive
    elif scenario == "queued_replacement":
        add_job(
            facts, "run-1", JobStatus.FAILED, created_at=T0, error_type="JobAbandoned"
        )
        add_job(facts, "run-1", JobStatus.QUEUED, created_at=NOW - HOUR)
        # the replacement itself is stalled by NOW - HOUR + 900 s
    elif scenario == "transient_budget_left":
        add_job(facts, "run-1", JobStatus.FAILED, created_at=T0, error_type="OSError")
    elif scenario == "succeeded_without_record":
        add_job(facts, "run-1", JobStatus.SUCCEEDED, created_at=T0)
    elif scenario == "cancelled":
        add_job(facts, "run-1", JobStatus.CANCELLED, created_at=T0)

    report = await lifecycle(store, facts)

    for entry in of(report, "run-1").values():
        assert entry.lifecycle_class == C.PENDING
        assert entry.reasons == expected_reasons
    assert report.summary.orphan_candidates.count == 0


async def test_young_unreferenced_recording_is_protected_by_the_pending_grace(
    store, facts
):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    store.modified[f"{ROOT}/run-1/recording.mcap"] = NOW - 2 * HOUR

    report = await lifecycle(store, facts, capture_report=capture_report())

    entry = of(report, "run-1")["recording"]
    assert entry.lifecycle_class == C.PENDING
    assert entry.reasons == ("pn1_within_pending_grace",)
    assert entry.age_seconds == 2 * 3600


async def test_between_the_graces_the_object_is_still_pending(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    store.modified[f"{ROOT}/run-1/recording.mcap"] = NOW - 3 * DAY

    report = await lifecycle(store, facts, capture_report=capture_report())

    assert of(report, "run-1")["recording"].reasons == ("within_orphan_grace",)
    assert report.summary.orphan_candidates.count == 0


async def test_grace_boundaries_are_inclusive_of_the_older_class(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    policy = ArtifactLifecyclePolicy(
        pending_grace=timedelta(hours=1), orphan_grace=timedelta(hours=2)
    )

    def stamp_and_classify(age):
        store.modified[f"{ROOT}/run-1/recording.mcap"] = NOW - age
        return lifecycle(store, facts, capture_report=capture_report(), policy=policy)

    assert of(await stamp_and_classify(timedelta(minutes=59)), "run-1")[
        "recording"
    ].reasons == ("pn1_within_pending_grace",)
    assert of(await stamp_and_classify(timedelta(hours=1)), "run-1")[
        "recording"
    ].reasons == ("within_orphan_grace",)
    assert (
        of(await stamp_and_classify(timedelta(hours=2)), "run-1")[
            "recording"
        ].lifecycle_class
        == C.ORPHAN_CANDIDATE
    )


async def test_a_modification_time_in_the_future_counts_as_brand_new(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    store.modified[f"{ROOT}/run-1/recording.mcap"] = NOW + 5 * DAY

    report = await lifecycle(store, facts, capture_report=capture_report())

    entry = of(report, "run-1")["recording"]
    assert entry.age_seconds == 0
    assert entry.reasons == ("pn1_within_pending_grace",)


@pytest.mark.parametrize(
    "classification",
    [
        CaptureClass.FINALIZED_WITH_RECEIPT,
        CaptureClass.FINALIZED_NO_RECEIPT,
        CaptureClass.CAPTURE_UNFINISHED,
    ],
)
async def test_a_surviving_capture_source_protects_an_old_recording(
    store, facts, classification
):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING

    report = await lifecycle(
        store, facts, capture_report=capture_report(capture("run-1", classification))
    )

    entry = of(report, "run-1")["recording"]
    assert entry.lifecycle_class == C.PENDING
    assert entry.reasons == ("pn3_capture_source_present",)


async def test_without_a_capture_report_an_old_recording_is_not_a_candidate(
    store, facts
):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING

    report = await lifecycle(store, facts)  # capture volume not observed

    entry = of(report, "run-1")["recording"]
    assert entry.lifecycle_class == C.PENDING
    assert entry.reasons == ("pn3_capture_source_unobserved",)
    assert report.policy.capture_observed is False


# ── orphan candidates: O1 / O2 ────────────────────────────────────────────────


async def test_old_recording_without_manifest_and_no_source_is_an_o1_candidate(
    store, facts
):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING

    report = await lifecycle(store, facts, capture_report=capture_report())

    entry = of(report, "run-1")["recording"]
    assert entry.lifecycle_class == C.ORPHAN_CANDIDATE
    assert entry.orphan_reason == OrphanReason.RECORDING_WITHOUT_MANIFEST
    assert entry.risk == RiskTier.HIGH == ORPHAN_RISK[entry.orphan_reason]
    assert entry.reasons == ("recording_without_manifest",)
    assert entry.acquisition_state == "publication_incomplete"
    summary = report.summary.orphan_candidates
    assert (summary.count, summary.bytes) == (1, len(RECORDING))
    assert summary.by_reason["recording_without_manifest"].count == 1
    assert summary.by_risk["high"].bytes == len(RECORDING)


@pytest.mark.parametrize(
    "jobs",
    [
        [(JobStatus.FAILED, "RecordingVerificationError")],
        [(JobStatus.FAILED, "OSError")] * REGISTRATION_ATTEMPT_BUDGET,  # budget spent
    ],
)
async def test_old_pair_whose_registration_failed_permanently_is_an_o2_candidate(
    store, facts, jobs
):
    publish(store, "run-1")
    for index, (status, error_type) in enumerate(jobs):
        add_job(
            facts, "run-1", status, created_at=T0 + index * HOUR, error_type=error_type
        )

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.ORPHAN_CANDIDATE}
    assert {e.orphan_reason for e in entries.values()} == {
        OrphanReason.RECORDING_MANIFEST_PERMANENTLY_FAILED
    }
    assert {e.risk for e in entries.values()} == {RiskTier.HIGH}
    assert entries["recording"].acquisition_state == "registration_failed_permanent"
    assert report.summary.orphan_candidates.count == 2


async def test_o2_needs_no_capture_observation_but_still_respects_the_graces(
    store, facts
):
    publish(store, "old")
    add_job(facts, "old", JobStatus.FAILED, error_type="RecordingVerificationError")
    publish(store, "young")
    restamp(store, "young", NOW - 2 * HOUR)
    add_job(facts, "young", JobStatus.FAILED, error_type="RecordingVerificationError")
    publish(store, "middle")
    restamp(store, "middle", NOW - 3 * DAY)
    add_job(facts, "middle", JobStatus.FAILED, error_type="RecordingVerificationError")

    report = await lifecycle(store, facts)  # no capture report

    assert set(classes(report, "old").values()) == {C.ORPHAN_CANDIDATE}
    assert {e.reasons for e in of(report, "young").values()} == {
        ("pn1_within_pending_grace",)
    }
    assert {e.reasons for e in of(report, "middle").values()} == {
        ("within_orphan_grace",)
    }


async def test_run_age_is_the_newest_object_of_the_publication(store, facts):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="RecordingVerificationError")
    store.modified[f"{ROOT}/run-1/recording.mcap"] = T0  # old
    store.modified[f"{ROOT}/run-1/robot_run_manifest.json"] = NOW - HOUR  # fresh

    report = await lifecycle(store, facts)

    # Both objects of the pair are protected by the manifest's age.
    assert {e.reasons for e in of(report, "run-1").values()} == {
        ("pn1_within_pending_grace",)
    }


# ── conservative: malformed / conflicting publication ─────────────────────────


async def test_aged_malformed_manifest_is_an_incident_never_a_candidate(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    store.objects[f"{ROOT}/run-1/robot_run_manifest.json"] = b"{truncated"

    report = await lifecycle(store, facts, capture_report=capture_report())

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    assert any(
        r.startswith("RobotRunManifestError") for r in entries["manifest"].reasons
    )
    assert report.summary.orphan_candidates.count == 0


async def test_aged_manifest_without_its_recording_is_an_incident(store, facts):
    publish(store, "run-1")
    del store.objects[f"{ROOT}/run-1/recording.mcap"]

    report = await lifecycle(store, facts, capture_report=capture_report())

    entry = of(report, "run-1")["manifest"]
    assert entry.lifecycle_class == C.INTEGRITY_INCIDENT
    assert "manifest_without_recording" in entry.reasons


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (
            lambda s: s.__setitem__(f"{ROOT}/run-1/recording.mcap", RECORDING + b"x"),
            "recording_size_mismatch",
        ),
        (
            lambda s: s.__setitem__(
                f"{ROOT}/run-1/recording.mcap", b"Z" * len(RECORDING)
            ),
            "recording_checksum_mismatch",
        ),
    ],
)
async def test_conflicting_unregistered_publication_is_an_incident(
    store, facts, mutate, reason
):
    publish(store, "run-1")
    mutate(store.objects)

    report = await lifecycle(store, facts, capture_report=capture_report())

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    assert reason in entries["recording"].reasons
    assert report.summary.orphan_candidates.count == 0


async def test_an_active_job_on_a_malformed_manifest_is_noted_not_trusted(store, facts):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = RECORDING
    store.objects[f"{ROOT}/run-1/robot_run_manifest.json"] = b"{truncated"
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=NOW - HOUR)

    report = await lifecycle(store, facts)

    entry = of(report, "run-1")["manifest"]
    assert entry.lifecycle_class == C.INTEGRITY_INCIDENT
    assert "active_registration_job" in entry.reasons


# ── reverse integrity: records whose objects are gone or contradict them ──────


async def test_registered_run_whose_recording_object_disappeared_is_an_incident(
    store, facts
):
    register(facts, "run-1", publish(store, "run-1"))
    del store.objects[f"{ROOT}/run-1/recording.mcap"]

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert entries["manifest"].lifecycle_class == C.INTEGRITY_INCIDENT
    assert "registered_recording_object_missing" in entries["manifest"].reasons
    dangling = entries["artifact_record"]
    assert dangling.subject == EntrySubject.ARTIFACT_RECORD
    assert dangling.lifecycle_class == C.INTEGRITY_INCIDENT
    assert dangling.reasons == ("dangling_reference",)
    assert dangling.uri == f"{ROOT}/run-1/recording.mcap"
    assert dangling.artifact_ids == (robot_run_recording_artifact_id("run-1"),)
    assert report.summary.referenced.count == 0
    assert report.summary.integrity_incidents.by_reason["dangling_reference"] == 1


async def test_registered_run_whose_manifest_object_disappeared_is_an_incident(
    store, facts
):
    register(facts, "run-1", publish(store, "run-1"))
    del store.objects[f"{ROOT}/run-1/robot_run_manifest.json"]

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert entries["recording"].lifecycle_class == C.INTEGRITY_INCIDENT
    assert "registered_manifest_missing" in entries["recording"].reasons
    assert entries["artifact_record"].uri == manifest_uri("run-1")
    assert report.summary.referenced.count == 0


async def test_registered_run_with_every_object_gone_is_still_reported(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    store.objects.clear()

    report = await lifecycle(store, facts)

    run_entry = next(
        e for e in report.entries if e.subject == EntrySubject.ROBOT_RUN_RECORD
    )
    assert run_entry.run_id == "run-1"
    assert run_entry.lifecycle_class == C.INTEGRITY_INCIDENT
    assert run_entry.reasons == ("registered_manifest_missing",)
    assert sorted(run_entry.artifact_ids) == sorted(
        [
            robot_run_recording_artifact_id("run-1"),
            robot_run_manifest_artifact_id("run-1"),
        ]
    )
    dangling = [e for e in report.entries if e.subject == EntrySubject.ARTIFACT_RECORD]
    assert len(dangling) == 2
    assert report.summary.integrity_incidents.count == 3
    assert report.summary.integrity_incidents.by_subject == {
        "artifact_record": 2,
        "robot_run_record": 1,
    }
    assert report.summary.referenced.count == 0


async def test_artifact_record_with_no_backing_object_is_dangling(store, facts):
    facts.artifacts["stray-record"] = ArtifactRecord(
        artifact_id="stray-record",
        kind="robot_run_recording",
        uri=f"{ROOT}/run-gone/recording.mcap",
        size_bytes=10,
    )

    report = await lifecycle(store, facts)

    (entry,) = report.entries
    assert entry.subject == EntrySubject.ARTIFACT_RECORD
    assert entry.run_id == "run-gone"
    assert entry.reasons == ("dangling_reference",)
    assert entry.size_bytes == 10


async def test_records_outside_the_root_are_not_this_scans_business(store, facts):
    facts.artifacts["elsewhere"] = ArtifactRecord(
        artifact_id="elsewhere", kind="scene_manifest", uri="s3://bucket/scenes/x.json"
    )
    facts.artifacts["sibling"] = ArtifactRecord(
        artifact_id="sibling", kind="x", uri=f"{ROOT}-other/run/recording.mcap"
    )

    report = await lifecycle(store, facts)

    assert report.entries == ()


async def test_a_run_registered_under_another_root_is_not_this_roots_incident(
    store, facts
):
    # Its records point at a different root, so its absence from this listing
    # says nothing: the mechanism that once flagged a real baseline run.
    register(facts, "elsewhere", publish(MemoryStore(), "elsewhere"))
    for artifact_id, record in list(facts.artifacts.items()):
        facts.artifacts[artifact_id] = record.model_copy(
            update={"uri": record.uri.replace(ROOT, "s3://bucket/other_root")}
        )

    report = await lifecycle(store, facts)

    assert report.entries == ()
    assert report.summary.integrity_incidents.count == 0


async def test_registered_run_with_its_artifact_records_missing_is_an_incident(
    store, facts
):
    register(facts, "run-1", publish(store, "run-1"))
    facts.artifacts.clear()

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    assert "registered_artifact_record_missing" in entries["recording"].reasons


async def test_artifact_records_without_a_robot_run_make_the_objects_incidents(
    store, facts
):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    del facts.runs["run-1"]  # RobotRunRecord gone, ArtifactRecords remain

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    assert entries["recording"].reasons == ("artifact_record_without_robot_run",)


async def test_record_whose_size_contradicts_the_object_is_an_incident(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    facts.artifacts[robot_run_recording_artifact_id("run-1")] = ArtifactRecord(
        artifact_id=robot_run_recording_artifact_id("run-1"),
        kind="robot_run_recording",
        uri=f"{ROOT}/run-1/recording.mcap",
        size_bytes=len(RECORDING) + 1,
        checksum=sha256_checksum(RECORDING),
    )

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    assert "referenced_size_mismatch" in entries["recording"].reasons
    assert "artifact_record_recording_mismatch" in entries["recording"].reasons


async def test_foreign_record_whose_checksum_contradicts_the_manifest_is_corrupt(
    store, facts
):
    publish(store, "run-1")
    facts.artifacts["foreign"] = ArtifactRecord(
        artifact_id="foreign",
        kind="robot_run_manifest",
        uri=manifest_uri("run-1"),
        checksum="sha256:" + "0" * 64,
    )

    report = await lifecycle(store, facts)

    entry = of(report, "run-1")["manifest"]
    assert entry.lifecycle_class == C.INTEGRITY_INCIDENT
    assert entry.reasons == ("referenced_corrupt",)


async def test_manifest_replaced_after_registration_is_an_incident(store, facts):
    publish(store, "run-1")
    register(facts, "run-1", "sha256:" + "0" * 64)  # record says a different manifest

    report = await lifecycle(store, facts)

    entries = of(report, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    # The run-level conflict and the record-level contradiction are both named.
    assert entries["manifest"].reasons == (
        "manifest_checksum_differs",
        "referenced_corrupt",
    )
    assert entries["manifest"].acquisition_state == "permanent_conflict"


async def test_same_size_corruption_of_a_registered_recording_needs_the_byte_check(
    store, facts
):
    register(facts, "run-1", publish(store, "run-1"))
    store.objects[f"{ROOT}/run-1/recording.mcap"] = b"Z" * len(RECORDING)

    listing_only = await lifecycle(store, facts)
    hashed = await lifecycle(store, facts, verify_recording_bytes=True)

    # Default: registration verified the bytes once and a registered recording
    # is compared by size only, so same-size corruption is not seen.
    assert set(classes(listing_only, "run-1").values()) == {C.REFERENCED}
    assert listing_only.policy.registered_recordings_hashed is False
    assert hashed.policy.registered_recordings_hashed is True
    entries = of(hashed, "run-1")
    assert {e.lifecycle_class for e in entries.values()} == {C.INTEGRITY_INCIDENT}
    assert "recording_checksum_mismatch" in entries["recording"].reasons


# ── objects the 12.5 contract does not classify ───────────────────────────────


async def test_unreferenced_unrecognized_objects_are_reported_not_classified(
    store, facts
):
    register(facts, "run-1", publish(store, "run-1"))
    store.objects[f"{ROOT}/run-1/notes.txt"] = b"hello"
    store.objects[f"{ROOT}/stray.bin"] = b"stray"
    store.objects[f"{ROOT}/run-2/only-notes.txt"] = b"notes"

    report = await lifecycle(store, facts)

    assert {
        (u.uri.removeprefix(ROOT + "/"), u.reason) for u in report.unclassified
    } == {
        ("run-1/notes.txt", "unrecognized_object_under_run_prefix"),
        ("stray.bin", "not_under_run_prefix"),
        ("run-2/only-notes.txt", "no_recording_or_manifest"),
    }
    assert report.summary.unclassified.count == 3
    assert report.summary.unclassified.bytes == len(b"hello") + len(b"stray") + len(
        b"notes"
    )
    # Never a candidate, whatever their age.
    assert report.summary.orphan_candidates.count == 0
    assert report.summary.referenced.count == 2


async def test_referenced_unrecognized_object_is_referenced(store, facts):
    store.objects[f"{ROOT}/stray.bin"] = b"stray"
    facts.artifacts["payload"] = ArtifactRecord(
        artifact_id="payload", kind="x", uri=f"{ROOT}/stray.bin", size_bytes=5
    )

    report = await lifecycle(store, facts)

    (entry,) = report.entries
    assert entry.lifecycle_class == C.REFERENCED
    assert entry.role.value == "other"
    assert report.unclassified == ()


# ── report shape, determinism, read-only ──────────────────────────────────────


def _mixed_system(store: MemoryStore, facts: FakeFacts) -> None:
    register(facts, "ok", publish(store, "ok"))
    publish(store, "pending")
    publish(store, "failed")
    add_job(facts, "failed", JobStatus.FAILED, error_type="RecordingVerificationError")
    store.objects[f"{ROOT}/o1/recording.mcap"] = RECORDING
    store.modified[f"{ROOT}/o1/recording.mcap"] = NOW - 10 * DAY
    store.objects[f"{ROOT}/young/recording.mcap"] = RECORDING
    store.modified[f"{ROOT}/young/recording.mcap"] = NOW - 2 * HOUR
    register(facts, "gone", publish(store, "gone"))
    del store.objects[f"{ROOT}/gone/recording.mcap"]
    store.objects[f"{ROOT}/stray.bin"] = b"x"


async def test_report_aggregates(store, facts):
    _mixed_system(store, facts)

    report = await lifecycle(store, facts, capture_report=capture_report())
    summary = report.summary

    assert summary.referenced.count == 2  # ok: recording + manifest
    assert summary.referenced.bytes > 0
    assert summary.pending.count == 3  # pending pair + young recording
    assert summary.pending.by_reason["pn2_registration_unfinished"].count == 2
    assert summary.pending.by_reason["pn1_within_pending_grace"].count == 1
    assert summary.pending.oldest_age_seconds == 30 * 86400
    assert summary.orphan_candidates.count == 3  # failed pair + o1 recording
    assert (
        summary.orphan_candidates.by_reason[
            "recording_manifest_permanently_failed"
        ].count
        == 2
    )
    assert summary.orphan_candidates.by_reason["recording_without_manifest"].count == 1
    assert summary.orphan_candidates.by_risk["high"].count == 3
    assert summary.orphan_candidates.bytes == sum(
        e.size_bytes for e in report.entries if e.lifecycle_class == C.ORPHAN_CANDIDATE
    )
    # gone: manifest object + dangling recording record
    assert summary.integrity_incidents.count == 2
    assert summary.integrity_incidents.object_bytes > 0
    assert summary.unclassified.count == 1
    assert report.policy.pending_grace_seconds == 86400
    assert report.policy.orphan_grace_seconds == 7 * 86400
    assert report.policy.stall_threshold_seconds == 900
    assert report.policy.attempt_budget == REGISTRATION_ATTEMPT_BUDGET
    assert report.observed_at == NOW


async def test_every_entry_has_exactly_one_class_and_the_totals_add_up(store, facts):
    _mixed_system(store, facts)

    report = await lifecycle(store, facts, capture_report=capture_report())
    summary = report.summary

    assert len(report.entries) == (
        summary.referenced.count
        + summary.pending.count
        + summary.orphan_candidates.count
        + summary.integrity_incidents.count
    )
    for entry in report.entries:
        if entry.lifecycle_class == C.REFERENCED:
            assert entry.reasons == () and entry.orphan_reason is None
        elif entry.lifecycle_class == C.ORPHAN_CANDIDATE:
            assert entry.orphan_reason is not None and entry.risk is not None
        else:
            assert entry.reasons
            assert entry.orphan_reason is None and entry.risk is None


async def test_report_is_deterministic_for_a_fixed_observation_time(store, facts):
    _mixed_system(store, facts)

    first = await lifecycle(store, facts, capture_report=capture_report())
    second = await lifecycle(store, facts, capture_report=capture_report())

    assert first.model_dump_json() == second.model_dump_json()
    assert first.to_json_dict() == second.to_json_dict()
    # Entries are ordered, not left to dict iteration.
    keys = [(e.subject.value, e.run_id or "", e.uri or "") for e in first.entries]
    assert keys == sorted(
        keys,
        key=lambda k: (
            ["object", "artifact_record", "robot_run_record"].index(k[0]),
            k[1],
            k[2],
        ),
    )


async def test_observation_time_is_part_of_the_report_and_moves_only_ages(store, facts):
    _mixed_system(store, facts)

    early = await lifecycle(store, facts, now=NOW, capture_report=capture_report())
    later = await lifecycle(
        store, facts, now=NOW + 400 * DAY, capture_report=capture_report()
    )

    assert early.observed_at != later.observed_at
    # Once everything is past both graces the young recording matures into a
    # candidate; protected-forever classes do not change.
    by_early = {(e.uri, e.run_id): e for e in early.entries if e.uri}
    young = (f"{ROOT}/young/recording.mcap", "young")
    assert by_early[young].lifecycle_class == C.PENDING
    assert {(e.uri, e.run_id): e for e in later.entries if e.uri}[
        young
    ].lifecycle_class == C.ORPHAN_CANDIDATE
    for key, entry in by_early.items():
        if entry.run_id == "pending":
            assert {(e.uri, e.run_id): e for e in later.entries if e.uri}[
                key
            ].lifecycle_class == C.PENDING


async def test_classification_changes_nothing(store, facts):
    _mixed_system(store, facts)
    objects = dict(store.objects)
    runs, artifacts = copy.deepcopy(facts.runs), copy.deepcopy(facts.artifacts)
    jobs = copy.deepcopy(facts.jobs)

    await lifecycle(store, facts, capture_report=capture_report())

    # The store double raises on any write/delete, so reaching here proves it.
    assert store.objects == objects
    assert (facts.runs, facts.artifacts, facts.jobs) == (runs, artifacts, jobs)


async def test_report_round_trips_through_json(store, facts):
    _mixed_system(store, facts)

    report = await lifecycle(store, facts, capture_report=capture_report())

    assert ArtifactLifecycleReport.model_validate(report.to_json_dict()) == report


async def test_a_finding_whose_object_appears_during_the_scan_is_dropped(facts):
    class RacingStore(MemoryStore):
        """Lists as if a publication had not happened yet; it exists by the time
        the dangling record is confirmed."""

        hidden: str

        async def list_objects(self, uri):
            return [o for o in await super().list_objects(uri) if o.uri != self.hidden]

    store = RacingStore()
    register(facts, "run-1", publish(store, "run-1"))
    store.hidden = f"{ROOT}/run-1/recording.mcap"

    report = await lifecycle(store, facts)

    assert not [e for e in report.entries if e.subject == EntrySubject.ARTIFACT_RECORD]
    assert report.unconfirmed_findings == 1


async def test_a_database_only_run_that_is_actually_present_is_not_reported(facts):
    class BlindStore(MemoryStore):
        async def list_objects(self, uri):
            return []

    store = BlindStore()
    register(facts, "run-1", publish(store, "run-1"))

    report = await lifecycle(store, facts)

    assert report.entries == ()
    assert report.unconfirmed_findings >= 1


# ── policy / settings ─────────────────────────────────────────────────────────


def test_grace_policy_is_validated():
    with pytest.raises(ValueError):
        ArtifactLifecyclePolicy(pending_grace=timedelta(0))
    with pytest.raises(ValueError):
        ArtifactLifecyclePolicy(
            pending_grace=timedelta(days=2), orphan_grace=timedelta(days=1)
        )
    ArtifactLifecyclePolicy(
        pending_grace=timedelta(days=1), orphan_grace=timedelta(days=1)
    )


async def test_naive_observation_time_is_rejected(store, facts):
    with pytest.raises(ValueError):
        await lifecycle(store, facts, now=datetime(2026, 10, 6))


def test_grace_defaults_and_overrides(monkeypatch):
    assert ApiSettings().artifact_lifecycle.pending_grace_seconds == 24 * 3600
    assert ApiSettings().artifact_lifecycle.orphan_grace_seconds == 7 * 24 * 3600
    monkeypatch.setenv("SCENEOPS_API_ARTIFACT_LIFECYCLE__ORPHAN_GRACE_SECONDS", "123")
    assert ApiSettings().artifact_lifecycle.orphan_grace_seconds == 123


# ── the reconciler extension this report relies on ────────────────────────────


async def test_reconcile_once_sees_database_only_runs_only_when_asked(store, facts):
    register(facts, "run-1", publish(store, "run-1"))
    store.objects.clear()

    default = await reconcile_once(
        artifact_store=store, root_uri=ROOT, registration_facts=facts.scope()
    )
    extended = await reconcile_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        include_database_runs=True,
    )

    assert default.runs == ()
    (run,) = extended.runs
    assert run.state == AcquisitionState.INTEGRITY_INCIDENT
    assert run.reasons == ("registered_manifest_missing",)


async def test_reconcile_once_hashes_registered_recordings_only_when_asked(
    store, facts
):
    register(facts, "run-1", publish(store, "run-1"))
    store.objects[f"{ROOT}/run-1/recording.mcap"] = b"Z" * len(RECORDING)

    default = await reconcile_once(
        artifact_store=store, root_uri=ROOT, registration_facts=facts.scope()
    )
    hashed = await reconcile_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        verify_registered_recordings=True,
    )

    assert default.runs[0].state == AcquisitionState.REGISTERED
    assert hashed.runs[0].state == AcquisitionState.INTEGRITY_INCIDENT
