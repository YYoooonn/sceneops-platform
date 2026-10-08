"""Phase 12.3 vertical: durable acquisition state -> ``reconcile --once`` ->
classifications -> again -> identical state and report, on real MinIO and real
PostgreSQL (ADR-008 §8, step 12.3).

Every durable state is constructed through the real components: the real
Recording Publisher (including ``publish_from_capture``), the real
``register_robot_run`` registrar, a real capture-volume layout scanned by the
real ``scan-capture``, and real Job rows. Objects are then damaged the way
crashes and operators damage them (deleted, truncated, overwritten). The
reconciler under test is the API's; this module lives with the worker's
real-infrastructure fixtures because the registered state is the worker's.

Requires SCENEOPS_DATABASE_URL (migrated to head) and MinIO
(`make test-integration` against `make local-up`); skips otherwise.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_acquisition.reconciliation import (
    AcquisitionState as S,
    ClassificationPolicy,
    postgres_registration_facts,
    reconcile_once,
    register_robot_run_execution_key,
)
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_recording.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    FinalizationReason,
    ReceiptFinalization,
    ReceiptKafka,
    ReceiptRecording,
)
from sceneops_core.robots.manifest import (
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    RecordingFormat,
    load_canonical_robot_run_manifest,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_recording import (
    derive_mcap_facts,
    publish_from_capture,
    scan_capture_volume,
)
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.robots.registration import register_robot_run

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"
_OTHER_MCAP = _FIXTURES_DIR / "nav_msgs_odometry.mcap"
_T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


# ── construction helpers ──────────────────────────────────────────────────────


def _capture_receipt(run_id: str, robot_id: str, data: bytes) -> CaptureReceipt:
    facts = derive_mcap_facts(io.BytesIO(data), source_clock="mcap_log_time")
    return CaptureReceipt(
        run_id=run_id,
        robot_id=robot_id,
        robot_platform="replay",
        recording=ReceiptRecording(
            file=f"{run_id}_0.mcap",
            format=RecordingFormat.MCAP,
            checksum=sha256_checksum(data),
            size_bytes=len(data),
        ),
        capture=CaptureInfo(
            source=CaptureSource(
                kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.telemetry.v1"]
            ),
            source_clock="mcap_log_time",
        ),
        message_count=facts.message_count,
        per_channel_counts={c.topic: c.message_count for c in facts.channels},
        finalization=ReceiptFinalization(
            reason=FinalizationReason.EXPLICIT_RUN_END, finalized_at=_T0
        ),
        kafka=ReceiptKafka(
            partition=0,
            first_offset=0,
            last_offset=facts.message_count,
            first_sequence=0,
            last_sequence=facts.message_count - 1,
        ),
    )


def _finalized_capture(
    capture_root: Path, run_id: str, robot_id: str, *, receipt: bool = True
) -> Path:
    data = _VALID_MCAP.read_bytes()
    directory = capture_root / run_id
    directory.mkdir(parents=True)
    (directory / f"{run_id}_0.mcap").write_bytes(data)
    if receipt:
        (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(
            _capture_receipt(run_id, robot_id, data).to_canonical_bytes()
        )
    return directory


def _partial_capture(capture_root: Path, run_id: str) -> None:
    directory = capture_root / ".partial" / run_id
    directory.mkdir(parents=True)
    (directory / f"{run_id}_0.mcap").write_bytes(b"half-written")


async def _add_job(
    session_factory,
    manifest_uri: str,
    status: JobStatus,
    *,
    job_id: str,
    created_at: datetime,
    error_type: str | None = None,
) -> None:
    async with session_factory() as session:
        await PostgresJobRepository(session).create(
            JobManifest(
                job_id=job_id,
                type=JobType.REGISTER_ROBOT_RUN,
                status=status,
                params={"manifest_uri": manifest_uri},
                execution_key=register_robot_run_execution_key(manifest_uri),
                error=ErrorInfo(type=error_type, message="injected")
                if error_type
                else None,
                queued_at=created_at,
                created_at=created_at,
                started_at=created_at if status != JobStatus.PENDING else None,
            )
        )
        await session.commit()


async def _durable_snapshot(
    session_factory, store: S3ArtifactStore, root_uri: str, run_ids, robot_id
):
    """Everything reconciliation could possibly touch, as plain data."""
    keys = [
        register_robot_run_execution_key(f"{root_uri}/{run_id}/robot_run_manifest.json")
        for run_id in run_ids
    ]
    async with session_factory() as session:

        async def rows(sql: str, **params) -> list[dict]:
            result = await session.execute(text(sql), params)
            return [row[0] for row in result.all()]

        database = {
            "robots": await rows(
                "SELECT to_jsonb(t) FROM robots t WHERE robot_id = :r ORDER BY 1::text",
                r=robot_id,
            ),
            "robot_runs": await rows(
                "SELECT to_jsonb(t) FROM robot_runs t WHERE run_id = ANY(:ids) "
                "ORDER BY run_id",
                ids=list(run_ids),
            ),
            "artifacts": await rows(
                "SELECT to_jsonb(t) FROM artifacts t WHERE owner_id = ANY(:ids) "
                "ORDER BY artifact_id",
                ids=list(run_ids),
            ),
            "jobs": await rows(
                "SELECT to_jsonb(t) FROM jobs t WHERE execution_key = ANY(:keys) "
                "ORDER BY job_id",
                keys=keys,
            ),
            "job_events": await rows(
                "SELECT to_jsonb(t) FROM job_events t WHERE job_id IN "
                "(SELECT job_id FROM jobs WHERE execution_key = ANY(:keys)) "
                "ORDER BY event_id",
                keys=keys,
            ),
        }
        await session.rollback()
    objects = [
        (o.uri, o.size_bytes, o.last_modified.isoformat())
        for o in await store.list_objects(root_uri)
    ]
    return {"database": database, "objects": objects}


# ── the vertical ──────────────────────────────────────────────────────────────


async def test_reconcile_once_classifies_known_durable_states_and_changes_nothing(
    worker_context, worker_settings, db_session, unique_id, tmp_path
) -> None:
    from sceneops_recording import publish_recording

    session_factory = get_async_sessionmaker()
    store = S3ArtifactStore(settings=worker_settings.artifact)
    root = worker_settings.artifact.robot_run_root_uri
    robot_id = unique_id("robot")
    capture_root = tmp_path / "capture"
    capture_root.mkdir()

    names = {
        label: unique_id(label)
        for label in (
            "registered",
            "pending",
            "active",
            "transient",
            "permanent",
            "succeeded",
            "conflict",
            "tampered_size",
            "tampered_bytes",
            "recording_only",
            "manifest_only",
            "malformed",
            "orphan_artifact",
            "resumable",
            "live",
            "ready",
            "legacy",
        )
    }

    async def publish(label: str, mcap: Path = _VALID_MCAP):
        return await publish_recording(
            artifact_store=store,
            root_uri=root,
            recording_path=mcap,
            run_id=names[label],
            robot_id=robot_id,
            robot_platform="replay",
            capture_source=CaptureSource(kind=CaptureSourceKind.FILE),
            source_clock="mcap_log_time",
        )

    def uri(label: str, name: str) -> str:
        return f"{root}/{names[label]}/{name}"

    # registered, via the real registrar
    published = await publish("registered")
    registration = await register_robot_run(
        context=worker_context, manifest_uri=published.manifest_uri, job_id="job-reg"
    )
    assert registration.created

    # published, with each kind of registration history
    for label in ("pending", "active", "transient", "permanent", "succeeded"):
        await publish(label)
    created = _T0
    await _add_job(
        session_factory,
        uri("active", "robot_run_manifest.json"),
        JobStatus.RUNNING,
        job_id=f"job-{names['active']}",
        created_at=created,
    )
    await _add_job(
        session_factory,
        uri("transient", "robot_run_manifest.json"),
        JobStatus.FAILED,
        job_id=f"job-{names['transient']}-1",
        created_at=created,
        error_type="OSError",
    )
    await _add_job(
        session_factory,
        uri("transient", "robot_run_manifest.json"),
        JobStatus.FAILED,
        job_id=f"job-{names['transient']}-2",
        created_at=created + timedelta(hours=1),
        error_type="ArtifactReadError",
    )
    await _add_job(
        session_factory,
        uri("permanent", "robot_run_manifest.json"),
        JobStatus.FAILED,
        job_id=f"job-{names['permanent']}",
        created_at=created,
        error_type="RecordingVerificationError",
    )
    await _add_job(
        session_factory,
        uri("succeeded", "robot_run_manifest.json"),
        JobStatus.SUCCEEDED,
        job_id=f"job-{names['succeeded']}",
        created_at=created,
    )

    # registered, then the manifest object is replaced by a different, valid one
    published = await publish("conflict")
    await register_robot_run(
        context=worker_context, manifest_uri=published.manifest_uri
    )
    other = load_canonical_robot_run_manifest(
        await store.read_bytes(published.manifest_uri)
    ).model_copy(update={"robot_platform": "someone-elses-platform"})
    await store.write_bytes(published.manifest_uri, other.to_canonical_bytes())

    # published, then the recording is damaged
    await publish("tampered_size")
    await store.write_bytes(
        uri("tampered_size", "recording.mcap"), _OTHER_MCAP.read_bytes()
    )
    await publish("tampered_bytes")
    original = _VALID_MCAP.read_bytes()
    await store.write_bytes(
        uri("tampered_bytes", "recording.mcap"),
        original[:-1] + bytes([original[-1] ^ 1]),
    )

    # crashes between publication steps
    await publish("recording_only")
    await store.delete_prefix(uri("recording_only", "robot_run_manifest.json"))
    await publish("manifest_only")
    await store.delete_prefix(uri("manifest_only", "recording.mcap"))
    await publish("malformed")
    await store.write_bytes(
        uri("malformed", "robot_run_manifest.json"), b'{"schema_ver'
    )

    # ArtifactRecord committed without its RobotRunRecord (never repaired)
    published = await publish("orphan_artifact")
    async with session_factory() as session:
        await PostgresArtifactRefRepository(session).create(
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
        await session.commit()

    # capture volume: published-from-capture then crashed before the manifest,
    # an unfinished capture, a finalized capture nobody published, a bag with
    # no receipt
    _finalized_capture(capture_root, names["resumable"], robot_id)
    await publish_from_capture(
        artifact_store=store,
        root_uri=root,
        capture_dir=capture_root / names["resumable"],
    )
    await store.delete_prefix(uri("resumable", "robot_run_manifest.json"))
    _partial_capture(capture_root, names["live"])
    _finalized_capture(capture_root, names["ready"], robot_id)
    _finalized_capture(capture_root, names["legacy"], robot_id, receipt=False)

    capture_report = scan_capture_volume(capture_root)
    run_ids = sorted(names.values())

    # ── reconcile twice around a snapshot of everything durable ──────────────
    before = await _durable_snapshot(session_factory, store, root, run_ids, robot_id)

    async def reconcile(**kwargs):
        return await reconcile_once(
            artifact_store=store,
            root_uri=root,
            registration_facts=postgres_registration_facts(session_factory),
            capture_report=capture_report,
            **kwargs,
        )

    # The snapshot sees what the scenarios built (so "unchanged" is meaningful).
    assert len(before["database"]["robot_runs"]) == 2  # registered, conflict
    assert len(before["database"]["artifacts"]) == 5  # 2 x 2 registered + 1 orphan
    assert len(before["database"]["jobs"]) == 5
    # 10 runs hold both objects; recording_only, manifest_only and resumable
    # one each; malformed both (the manifest is junk, not absent).
    assert len(before["objects"]) == 25

    first = await reconcile()
    second = await reconcile()
    after = await _durable_snapshot(session_factory, store, root, run_ids, robot_id)

    expected = {
        "registered": S.REGISTERED,
        "pending": S.REGISTRATION_PENDING,
        "active": S.REGISTRATION_ACTIVE,
        "transient": S.REGISTRATION_FAILED_TRANSIENT,
        "permanent": S.REGISTRATION_FAILED_PERMANENT,
        "succeeded": S.REGISTRATION_PENDING,
        "conflict": S.PERMANENT_CONFLICT,
        "tampered_size": S.INTEGRITY_INCIDENT,
        "tampered_bytes": S.INTEGRITY_INCIDENT,
        "recording_only": S.PUBLICATION_INCOMPLETE,
        "manifest_only": S.PUBLICATION_INCOMPLETE,
        "malformed": S.PUBLICATION_INCOMPLETE,
        "orphan_artifact": S.INTEGRITY_INCIDENT,
        "resumable": S.PUBLICATION_INCOMPLETE,
        "live": S.CAPTURE_UNFINISHED,
        "ready": S.PUBLISH_PENDING,
        "legacy": S.FINALIZED_NO_RECEIPT,
    }
    by_run = {run.run_id: run for run in first.runs}
    assert {label: by_run[name].state for label, name in names.items()} == expected
    assert set(by_run) == set(names.values())  # nothing else under this root

    def reasons(label: str):
        return by_run[names[label]].reasons

    assert reasons("conflict") == ("manifest_checksum_differs",)
    assert reasons("tampered_size") == ("recording_size_mismatch",)
    assert reasons("tampered_bytes") == ("recording_checksum_mismatch",)
    assert reasons("orphan_artifact") == ("artifact_record_without_robot_run",)
    assert reasons("recording_only") == (
        "no_capture_source",
        "recording_without_manifest",
    )
    assert reasons("manifest_only") == (
        "manifest_without_recording",
        "no_capture_source",
    )
    assert reasons("resumable") == (
        "recording_without_manifest",
        "resumable_from_capture",
    )
    assert reasons("transient") == ("error_type:ArtifactReadError",)
    assert reasons("permanent") == ("error_type:RecordingVerificationError",)
    assert reasons("succeeded") == (
        "latest_job_succeeded",
        "succeeded_job_without_robot_run",
    )
    assert any(r.startswith("RobotRunManifestError") for r in reasons("malformed"))
    transient = by_run[names["transient"]].registration
    assert transient.failed_job_count == 2
    assert [j.job_id for j in transient.jobs] == [
        f"job-{names['transient']}-1",
        f"job-{names['transient']}-2",
    ]
    registered = by_run[names["registered"]].registration
    assert registered.robot_run.manifest_checksum == (
        by_run[names["registered"]].publication.manifest_checksum
    )
    assert registered.robot_run.registered_at is not None
    ready = by_run[names["ready"]].capture
    assert ready.receipt.robot_id == robot_id and ready.final_present

    # ── nothing changed, and the report is reproducible ──────────────────────
    assert after == before
    assert first.model_dump_json() == second.model_dump_json()
    assert sum(first.counts.values()) == len(names)

    # A threshold read from the clock does not alter durable state either, and
    # promotes only the in-flight Job (created at _T0, long ago).
    stalled = await reconcile(
        policy=ClassificationPolicy(stall_candidate_after=timedelta(hours=1)),
        now=_T0 + timedelta(days=1),
    )
    states = {run.run_id: run.state for run in stalled.runs}
    assert states[names["active"]] == S.REGISTRATION_STALLED_CANDIDATE
    assert {n: s for n, s in states.items() if n != names["active"]} == {
        n: by_run[n].state for n in by_run if n != names["active"]
    }
    assert (
        await _durable_snapshot(session_factory, store, root, run_ids, robot_id)
        == before
    )

    # ── the one-shot entrypoint prints the same report without any server ────
    # The command always classifies with a stall threshold and the attempt
    # budget (its 900 s default is shorter than this fixture's Job age). A
    # threshold so large it never fires makes it comparable to ``first``.
    never = timedelta(days=365 * 100)
    capture_report_path = tmp_path / "capture_scan.json"
    capture_report_path.write_text(
        json.dumps(capture_report.model_dump(mode="json")), encoding="utf-8"
    )
    artifact = worker_settings.artifact
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sceneops_worker.main",
            "acquisition",
            "reconcile",
            "--once",
            "--capture-report",
            str(capture_report_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "SCENEOPS_WORKER_ARTIFACT__BACKEND": "minio",
            "SCENEOPS_WORKER_ARTIFACT__ROOT_URI": artifact.root_uri,
            "SCENEOPS_WORKER_ARTIFACT__ENDPOINT_URL": artifact.endpoint_url,
            "SCENEOPS_WORKER_ARTIFACT__ACCESS_KEY_ID": artifact.access_key_id,
            "SCENEOPS_WORKER_ARTIFACT__SECRET_ACCESS_KEY": artifact.secret_access_key,
            "SCENEOPS_WORKER_RECONCILER__STALL_THRESHOLD_SECONDS": str(
                never.total_seconds()
            ),
        },
    )
    assert completed.returncode == 0, completed.stderr
    expected = await reconcile(
        policy=ClassificationPolicy(stall_candidate_after=never, attempt_budget=3),
        now=_T0,
    )
    assert json.loads(completed.stdout) == expected.to_json_dict()
    assert expected.mode == "observe" and expected.actions == ()
    # Without the policy the states are exactly the ones observed above.
    assert {run.run_id: run.state for run in expected.runs} == {
        run.run_id: run.state for run in first.runs
    }
    assert (
        await _durable_snapshot(session_factory, store, root, run_ids, robot_id)
        == before
    )


async def test_the_database_scope_is_read_only_at_the_database(
    db_session, unique_id
) -> None:
    """The reconciler's transaction is declared READ ONLY, so PostgreSQL --
    not only this code -- rejects a write made inside it."""
    from sqlalchemy.exc import DBAPIError

    scope = postgres_registration_facts(get_async_sessionmaker())

    async with scope() as facts:
        assert await facts.robot_runs([unique_id("run")]) == {}
        session = facts._runs._session  # the scope's own session
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await session.execute(
                text("INSERT INTO robots (robot_id, status) VALUES (:id, 'active')"),
                {"id": unique_id("robot")},
            )
