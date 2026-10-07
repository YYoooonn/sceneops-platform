"""Phase 12.5 vertical: durable acquisition state -> artifact lifecycle report
-> referenced / pending / orphan candidate / integrity incident, on real MinIO
and real PostgreSQL (ADR-008 §6, §8 step 12.5).

Every durable state is built through the real components (the real Recording
Publisher including ``publish_from_capture``, the real ``register_robot_run``
registrar, a real capture-volume layout scanned by the real ``scan-capture``,
real Job rows) and then damaged the way crashes and operators damage it. The
store's own ``last_modified`` is real; grace periods are exercised by injecting
the observation time, so no test sleeps. The classifier under test is the
API's; this module lives with the worker's real-infrastructure fixtures because
the registered state is the worker's.

Requires SCENEOPS_DATABASE_URL (migrated to head) and MinIO
(`make test-integration` against `make local-up`); skips otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta


from app.domains.robots.artifact_lifecycle import (
    ArtifactLifecyclePolicy,
    artifact_lifecycle_once,
)
from app.domains.robots.reconciliation import postgres_registration_facts
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.robots.artifact_lifecycle import LifecycleClass as C
from sceneops_core.robots.manifest import (
    CaptureSource,
    CaptureSourceKind,
    load_canonical_robot_run_manifest,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_integrations.recording import publish_from_capture, scan_capture_volume
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.robots.registration import register_robot_run
from tests.robots.test_reconciliation_vertical_integration import (
    _OTHER_MCAP,
    _VALID_MCAP,
    _add_job,
    _durable_snapshot,
    _finalized_capture,
)

_JOB_TIME = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)


async def test_artifact_lifecycle_classifies_every_class_on_real_infrastructure(
    worker_context,
    worker_settings,
    unique_id,
    tmp_path,
) -> None:
    from sceneops_integrations.recording import publish_recording

    session_factory = get_async_sessionmaker()
    store = S3ArtifactStore(settings=worker_settings.artifact)
    root = worker_settings.artifact.robot_run_root_uri
    robot_id = unique_id("robot")
    capture_root = tmp_path / "capture"
    capture_root.mkdir()

    labels = (
        "registered",
        "same_size_corrupt",
        "pending",
        "active",
        "transient",
        "permanent",
        "budget_spent",
        "o1",
        "resumable",
        "malformed",
        "manifest_only",
        "tampered",
        "conflict",
        "recording_gone",
        "manifest_gone",
        "all_gone",
        "orphan_artifact",
        "foreign_dangling",
    )
    names = {label: unique_id(label) for label in labels}

    def uri(label: str, name: str) -> str:
        return f"{root}/{names[label]}/{name}"

    async def publish(label: str):
        return await publish_recording(
            artifact_store=store,
            root_uri=root,
            recording_path=_VALID_MCAP,
            run_id=names[label],
            robot_id=robot_id,
            robot_platform="replay",
            capture_source=CaptureSource(kind=CaptureSourceKind.FILE),
            source_clock="mcap_log_time",
        )

    async def publish_and_register(label: str):
        published = await publish(label)
        result = await register_robot_run(
            context=worker_context, manifest_uri=published.manifest_uri
        )
        assert result.created
        return published

    # ── referenced ─────────────────────────────────────────────────────────
    await publish_and_register("registered")
    await publish_and_register("same_size_corrupt")
    original = _VALID_MCAP.read_bytes()
    await store.write_bytes(
        uri("same_size_corrupt", "recording.mcap"),
        original[:-1] + bytes([original[-1] ^ 1]),
    )

    # ── pending: published pairs with each kind of registration history ────
    for label in ("pending", "active", "transient", "permanent", "budget_spent"):
        await publish(label)
    wall = datetime.now(UTC)
    await _add_job(
        session_factory,
        uri("active", "robot_run_manifest.json"),
        JobStatus.RUNNING,
        job_id=f"job-{names['active']}",
        created_at=wall,
    )
    await _add_job(
        session_factory,
        uri("transient", "robot_run_manifest.json"),
        JobStatus.FAILED,
        job_id=f"job-{names['transient']}",
        created_at=_JOB_TIME,
        error_type="OSError",
    )
    # ── orphan candidates (once old enough) ────────────────────────────────
    await _add_job(
        session_factory,
        uri("permanent", "robot_run_manifest.json"),
        JobStatus.FAILED,
        job_id=f"job-{names['permanent']}",
        created_at=_JOB_TIME,
        error_type="RecordingVerificationError",
    )
    for attempt in range(3):
        await _add_job(
            session_factory,
            uri("budget_spent", "robot_run_manifest.json"),
            JobStatus.FAILED,
            job_id=f"job-{names['budget_spent']}-{attempt}",
            created_at=_JOB_TIME + attempt * _HOUR,
            error_type="OSError",
        )
    await publish("o1")
    await store.delete_prefix(uri("o1", "robot_run_manifest.json"))

    _finalized_capture(capture_root, names["resumable"], robot_id)
    await publish_from_capture(
        artifact_store=store,
        root_uri=root,
        capture_dir=capture_root / names["resumable"],
    )
    await store.delete_prefix(uri("resumable", "robot_run_manifest.json"))
    capture_report = scan_capture_volume(capture_root)

    # ── integrity incidents ────────────────────────────────────────────────
    await publish("malformed")
    await store.write_bytes(
        uri("malformed", "robot_run_manifest.json"), b'{"schema_ver'
    )
    await publish("manifest_only")
    await store.delete_prefix(uri("manifest_only", "recording.mcap"))
    await publish("tampered")
    await store.write_bytes(uri("tampered", "recording.mcap"), _OTHER_MCAP.read_bytes())

    published = await publish_and_register("conflict")
    other = load_canonical_robot_run_manifest(
        await store.read_bytes(published.manifest_uri)
    ).model_copy(update={"robot_platform": "someone-elses-platform"})
    await store.write_bytes(published.manifest_uri, other.to_canonical_bytes())

    await publish_and_register("recording_gone")
    await store.delete_prefix(uri("recording_gone", "recording.mcap"))
    await publish_and_register("manifest_gone")
    await store.delete_prefix(uri("manifest_gone", "robot_run_manifest.json"))
    await publish_and_register("all_gone")
    await store.delete_prefix(f"{root}/{names['all_gone']}")

    published = await publish("orphan_artifact")
    async with session_factory() as session:
        repository = PostgresArtifactRefRepository(session)
        await repository.create(
            artifact_id=robot_run_recording_artifact_id(names["orphan_artifact"]),
            ref=ArtifactRef(
                kind=ArtifactKind.ROBOT_RUN_RECORDING,
                uri=published.recording_uri,
                size_bytes=published.manifest.recording.size_bytes,
                checksum=published.manifest.recording.checksum,
            ),
            owner_type="robot_run",
            owner_id=names["orphan_artifact"],
        )
        await repository.create(
            artifact_id=f"foreign-{names['foreign_dangling']}",
            ref=ArtifactRef(
                kind=ArtifactKind.ROBOT_RUN_RECORDING,
                uri=uri("foreign_dangling", "recording.mcap"),
                size_bytes=1,
            ),
            owner_type="robot_run",
            owner_id=names["foreign_dangling"],
        )
        await session.commit()

    # An object that belongs to no publication.
    await store.write_bytes(f"{root}/stray.bin", b"stray")

    run_ids = sorted(names.values())

    async def lifecycle(now: datetime, **kwargs):
        return await artifact_lifecycle_once(
            artifact_store=store,
            root_uri=root,
            registration_facts=postgres_registration_facts(session_factory),
            now=now,
            capture_report=capture_report,
            **kwargs,
        )

    def by_label(report, label: str) -> dict:
        found: dict[str, object] = {}
        for entry in report.entries:
            if entry.run_id == names[label]:
                key = entry.role.value if entry.role else entry.subject.value
                assert key not in found, (label, key)
                found[key] = entry
        return found

    def classes(report, label: str) -> dict[str, C]:
        return {k: e.lifecycle_class for k, e in by_label(report, label).items()}

    def reasons(report, label: str, key: str):
        return by_label(report, label)[key].reasons

    # ── the aged report ──────────────────────────────────────────────────────
    before = await _durable_snapshot(session_factory, store, root, run_ids, robot_id)
    aged_at = datetime.now(UTC) + 30 * _DAY
    aged = await lifecycle(aged_at)
    again = await lifecycle(aged_at)
    after = await _durable_snapshot(session_factory, store, root, run_ids, robot_id)

    assert after == before  # read only, on real PostgreSQL and MinIO
    assert aged.model_dump_json() == again.model_dump_json()  # fixed `now`
    assert aged.unconfirmed_findings == 0
    assert aged.policy.capture_observed is True
    assert {e.run_id for e in aged.entries} <= set(
        run_ids
    )  # nothing else under the root

    both = {"recording", "manifest"}
    # referenced
    assert classes(aged, "registered") == {k: C.REFERENCED for k in both}
    assert classes(aged, "same_size_corrupt") == {k: C.REFERENCED for k in both}
    # pending, at an age far beyond both graces: protected by registration
    for label in ("pending", "active", "transient"):
        assert classes(aged, label) == {k: C.PENDING for k in both}, label
        assert reasons(aged, label, "recording")[0] == "pn2_registration_unfinished"
    assert reasons(aged, "active", "recording") == (
        "pn2_registration_unfinished",
        "pn4_active_registration_job",
    )
    assert reasons(aged, "resumable", "recording") == ("pn3_capture_source_present",)
    # orphan candidates
    for label in ("permanent", "budget_spent"):
        assert classes(aged, label) == {k: C.ORPHAN_CANDIDATE for k in both}, label
        for entry in by_label(aged, label).values():
            assert entry.orphan_reason.value == "recording_manifest_permanently_failed"
            assert entry.risk.value == "high"
    (o1,) = by_label(aged, "o1").values()
    assert o1.lifecycle_class == C.ORPHAN_CANDIDATE
    assert o1.orphan_reason.value == "recording_without_manifest"
    # integrity incidents: conservative whatever the age
    assert classes(aged, "malformed") == {k: C.INTEGRITY_INCIDENT for k in both}
    assert classes(aged, "manifest_only") == {"manifest": C.INTEGRITY_INCIDENT}
    assert classes(aged, "tampered") == {k: C.INTEGRITY_INCIDENT for k in both}
    assert classes(aged, "conflict") == {k: C.INTEGRITY_INCIDENT for k in both}
    assert classes(aged, "orphan_artifact") == {k: C.INTEGRITY_INCIDENT for k in both}
    # reverse integrity: a reference whose object is gone is never healthy
    assert classes(aged, "recording_gone") == {
        "manifest": C.INTEGRITY_INCIDENT,
        "artifact_record": C.INTEGRITY_INCIDENT,
    }
    assert by_label(aged, "recording_gone")["artifact_record"].uri == uri(
        "recording_gone", "recording.mcap"
    )
    assert classes(aged, "manifest_gone") == {
        "recording": C.INTEGRITY_INCIDENT,
        "artifact_record": C.INTEGRITY_INCIDENT,
    }
    all_gone = [e for e in aged.entries if e.run_id == names["all_gone"]]
    assert sorted(e.subject.value for e in all_gone) == [
        "artifact_record",
        "artifact_record",
        "robot_run_record",
    ]
    assert classes(aged, "foreign_dangling") == {
        "artifact_record": C.INTEGRITY_INCIDENT
    }
    assert [u.uri for u in aged.unclassified] == [f"{root}/stray.bin"]

    # aggregates over the whole constructed state
    summary = aged.summary
    assert summary.referenced.count == 4  # registered + same_size_corrupt
    assert (
        summary.pending.count == 7
    )  # pending, active, transient (2 objects each) + resumable recording
    assert summary.orphan_candidates.count == 5  # permanent x2, budget x2, o1
    assert summary.orphan_candidates.by_risk["high"].count == 5
    assert summary.integrity_incidents.count == len(
        [e for e in aged.entries if e.lifecycle_class == C.INTEGRITY_INCIDENT]
    )
    assert summary.unclassified.count == 1
    assert summary.pending.oldest_age_seconds >= 30 * 86400

    # The same-size corruption is invisible to the listing-only default and is
    # found by the opt-in byte check.
    hashed = await lifecycle(aged_at, verify_recording_bytes=True)
    assert classes(hashed, "same_size_corrupt") == {
        k: C.INTEGRITY_INCIDENT for k in both
    }
    assert "recording_checksum_mismatch" in reasons(
        hashed, "same_size_corrupt", "recording"
    )
    assert classes(hashed, "registered") == {k: C.REFERENCED for k in both}

    # ── grace periods: only the injected time changes ──────────────────────
    wall_now = datetime.now(UTC)
    fresh = await lifecycle(wall_now + 10 * timedelta(minutes=1))
    assert fresh.summary.orphan_candidates.count == 0
    assert reasons(fresh, "o1", "recording") == ("pn1_within_pending_grace",)
    assert reasons(fresh, "permanent", "recording") == ("pn1_within_pending_grace",)
    middle = await lifecycle(wall_now + 3 * _DAY)
    assert middle.summary.orphan_candidates.count == 0
    assert reasons(middle, "o1", "recording") == ("within_orphan_grace",)
    # Referenced / pending-by-registration / incident classes do not move.
    for label in ("registered", "pending", "malformed", "conflict"):
        assert classes(fresh, label) == classes(aged, label), label
    tight = await lifecycle(
        wall_now + 10 * timedelta(minutes=1),
        policy=ArtifactLifecyclePolicy(
            pending_grace=timedelta(seconds=1), orphan_grace=timedelta(seconds=2)
        ),
    )
    assert reasons(tight, "o1", "recording") == ("recording_without_manifest",)

    # Without the capture volume an incomplete publication is protected, not O1.
    unobserved = await artifact_lifecycle_once(
        artifact_store=store,
        root_uri=root,
        registration_facts=postgres_registration_facts(session_factory),
        now=aged_at,
    )
    assert reasons(unobserved, "o1", "recording") == ("pn3_capture_source_unobserved",)
    assert unobserved.policy.capture_observed is False

    # ── the one-shot entrypoint prints the same report without any server ───
    capture_report_path = tmp_path / "capture_scan.json"
    capture_report_path.write_text(
        json.dumps(capture_report.model_dump(mode="json")), encoding="utf-8"
    )
    artifact = worker_settings.artifact
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.domains.robots.artifact_lifecycle",
            "--once",
            "--capture-report",
            str(capture_report_path),
            "--observed-at",
            aged_at.isoformat(),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "SCENEOPS_API_ARTIFACT__BACKEND": "minio",
            "SCENEOPS_API_ARTIFACT__ROOT_URI": artifact.root_uri,
            "SCENEOPS_API_ARTIFACT__ENDPOINT_URL": artifact.endpoint_url,
            "SCENEOPS_API_ARTIFACT__ACCESS_KEY_ID": artifact.access_key_id,
            "SCENEOPS_API_ARTIFACT__SECRET_ACCESS_KEY": artifact.secret_access_key,
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == aged.to_json_dict()
    assert (
        await _durable_snapshot(session_factory, store, root, run_ids, robot_id)
        == before
    )
