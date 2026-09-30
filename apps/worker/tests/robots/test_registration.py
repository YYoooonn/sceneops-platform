"""Unit tests for sceneops_worker.robots.registration -- no real Postgres,
no real ArtifactStore. Uses fake in-memory stores implementing just the
methods register_robot_run_capture actually calls, plus a real MCAP
fixture file (apps/worker/tests/fixtures/rosbag/can_replay_scene_0061.mcap)
for MCAP validation, since that logic reads real bytes with the real
``mcap`` package.

Real-Postgres + real-MinIO idempotency/conflict/concurrency coverage
lives in apps/worker/tests/robots/test_registration_integration.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.robots.schemas import RobotRunRecord, RobotRunStatus
from sceneops_worker.robots.artifacts import RobotRunArtifactWriteResult, _sha256_hex
from sceneops_worker.robots.registration import (
    InconsistentCanonicalStateError,
    RobotRunCaptureValidationError,
    RobotRunRegistrationConflictError,
    register_robot_run_capture,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"


class _FakeRobotStore:
    def __init__(self) -> None:
        self.runs: dict[str, RobotRunRecord] = {}
        self.robots: dict[str, object] = {}

    async def get_run(self, run_id: str) -> RobotRunRecord | None:
        return self.runs.get(run_id)

    async def upsert_robot(self, robot) -> None:
        self.robots[robot.robot_id] = robot

    async def create_run(self, run: RobotRunRecord) -> RobotRunRecord:
        if run.run_id in self.runs:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        self.runs[run.run_id] = run
        return run


class _FakeArtifactRecordStore:
    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return self.records.get(artifact_id)

    async def create(
        self, *, artifact_id: str, ref, owner_type=None, owner_id=None, **_kw
    ):
        if artifact_id in self.records:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=ref.kind.value if hasattr(ref.kind, "value") else ref.kind,
            uri=ref.uri,
            media_type=ref.media_type,
            size_bytes=ref.size_bytes,
            checksum=ref.checksum,
            owner_type=owner_type.value if hasattr(owner_type, "value") else owner_type,
            owner_id=owner_id,
            metadata=ref.metadata,
        )
        self.records[artifact_id] = record
        return record


class _FakeRobotRunArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def recording_uri(self, robot_run_id: str) -> str:
        return f"fake://robot_runs/{robot_run_id}/{robot_run_id}.mcap"

    async def exists(self, robot_run_id: str) -> bool:
        return robot_run_id in self.objects

    async def read_recording_bytes(self, robot_run_id: str) -> bytes:
        return self.objects[robot_run_id]

    async def write_recording(self, *, robot_run_id: str, data: bytes):
        self.objects[robot_run_id] = data
        return RobotRunArtifactWriteResult(
            uri=self.recording_uri(robot_run_id),
            checksum=f"sha256:{_sha256_hex(data)}",
            size_bytes=len(data),
        )


class _FakeContext:
    def __init__(self) -> None:
        self.robot_store = _FakeRobotStore()
        self.artifact_record_store = _FakeArtifactRecordStore()
        self.robot_run_artifact_store = _FakeRobotRunArtifactStore()
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


# ---------------------------------------------------------------------
# MCAP validation rejections
# ---------------------------------------------------------------------


async def test_rejects_partial_capture_path(tmp_path) -> None:
    partial_dir = tmp_path / ".partial" / "run-1"
    partial_dir.mkdir(parents=True)
    partial_path = partial_dir / "run-1_0.mcap"
    partial_path.write_bytes(b"irrelevant")

    with pytest.raises(RobotRunCaptureValidationError, match=r"\.partial"):
        await register_robot_run_capture(
            context=_FakeContext(),
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=partial_path,
        )


async def test_rejects_missing_file(tmp_path) -> None:
    missing = tmp_path / "does_not_exist.mcap"

    with pytest.raises(RobotRunCaptureValidationError, match="not found"):
        await register_robot_run_capture(
            context=_FakeContext(),
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=missing,
        )


async def test_rejects_corrupt_mcap(tmp_path) -> None:
    corrupt = tmp_path / "corrupt.mcap"
    corrupt.write_bytes(b"not a real mcap file" * 5)

    with pytest.raises(RobotRunCaptureValidationError, match="unreadable/corrupt"):
        await register_robot_run_capture(
            context=_FakeContext(),
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=corrupt,
        )


async def test_rejects_empty_mcap(tmp_path) -> None:
    empty = tmp_path / "empty.mcap"
    empty.write_bytes(b"")

    with pytest.raises(RobotRunCaptureValidationError):
        await register_robot_run_capture(
            context=_FakeContext(),
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=empty,
        )


# ---------------------------------------------------------------------
# First registration / exact retry / conflicts
# ---------------------------------------------------------------------


async def test_first_registration_uploads_and_creates_records() -> None:
    context = _FakeContext()

    registration = await register_robot_run_capture(
        context=context,
        robot_id="robot-1",
        robot_run_id="run-1",
        mcap_path=_VALID_MCAP,
    )

    assert registration.created is True
    assert registration.robot_run.run_id == "run-1"
    assert registration.robot_run.status == RobotRunStatus.COMPLETED
    assert registration.artifact.owner_type == ArtifactOwnerType.ROBOT_RUN.value
    assert registration.artifact.owner_id == "run-1"
    assert registration.artifact.checksum is not None
    assert context.committed is True
    assert "run-1" in context.robot_run_artifact_store.objects


async def test_exact_retry_returns_existing_state_and_creates_nothing() -> None:
    context = _FakeContext()
    first = await register_robot_run_capture(
        context=context, robot_id="robot-1", robot_run_id="run-1", mcap_path=_VALID_MCAP
    )

    second = await register_robot_run_capture(
        context=context, robot_id="robot-1", robot_run_id="run-1", mcap_path=_VALID_MCAP
    )

    assert second.created is False
    assert second.robot_run == first.robot_run
    assert second.artifact == first.artifact


async def test_same_robot_run_id_different_checksum_conflicts_without_mutating(
    tmp_path,
) -> None:
    context = _FakeContext()
    await register_robot_run_capture(
        context=context, robot_id="robot-1", robot_run_id="run-1", mcap_path=_VALID_MCAP
    )
    original_run = context.robot_store.runs["run-1"]

    # A second real, valid MCAP fixture with different content/checksum --
    # not a mutated copy of _VALID_MCAP, since validation must still pass.
    other_mcap = tmp_path / "other.mcap"
    other_mcap.write_bytes((_FIXTURES_DIR / "nav_msgs_odometry.mcap").read_bytes())

    with pytest.raises(RobotRunRegistrationConflictError, match="different checksum"):
        await register_robot_run_capture(
            context=context,
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=other_mcap,
        )

    assert context.robot_store.runs["run-1"] == original_run


async def test_orphaned_object_with_matching_checksum_is_reused() -> None:
    context = _FakeContext()
    data = _VALID_MCAP.read_bytes()
    # Simulate "upload succeeded, DB registration failed": the object is
    # already present in ArtifactStore, but no RobotRun/ArtifactRecord
    # exist yet.
    await context.robot_run_artifact_store.write_recording(
        robot_run_id="run-1", data=data
    )

    registration = await register_robot_run_capture(
        context=context, robot_id="robot-1", robot_run_id="run-1", mcap_path=_VALID_MCAP
    )

    assert registration.created is True
    assert registration.robot_run.run_id == "run-1"
    # Must not have re-uploaded (same bytes already present, count 1 write).
    assert context.robot_run_artifact_store.objects["run-1"] == data


async def test_orphaned_object_with_different_checksum_conflicts(tmp_path) -> None:
    context = _FakeContext()
    await context.robot_run_artifact_store.write_recording(
        robot_run_id="run-1",
        data=(_FIXTURES_DIR / "nav_msgs_odometry.mcap").read_bytes(),
    )

    with pytest.raises(
        RobotRunRegistrationConflictError, match="refusing to overwrite"
    ):
        await register_robot_run_capture(
            context=context,
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=_VALID_MCAP,
        )

    assert "run-1" not in context.robot_store.runs


async def test_integrity_error_race_resolves_to_existing_winner() -> None:
    """Simulates the concurrent-writer race directly: by the time this
    call reaches the DB write, another writer has already committed the
    same robot_run_id with the SAME content -- create_run raises
    IntegrityError, and the caller must resolve to that winner's state
    rather than propagating the error."""
    context = _FakeContext()
    data = _VALID_MCAP.read_bytes()
    checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"

    # Pre-seed as if a concurrent writer already fully committed.
    winner_run = RobotRunRecord(
        run_id="run-1", robot_id="robot-1", status=RobotRunStatus.COMPLETED
    )
    await context.robot_run_artifact_store.write_recording(
        robot_run_id="run-1", data=data
    )
    context.robot_store.runs["run-1"] = winner_run
    context.artifact_record_store.records["art-robotrun-run-1"] = ArtifactRecord(
        artifact_id="art-robotrun-run-1",
        kind=ArtifactKind.ROBOT_RUN_RECORDING.value,
        uri=context.robot_run_artifact_store.recording_uri("run-1"),
        checksum=checksum,
    )

    registration = await register_robot_run_capture(
        context=context, robot_id="robot-1", robot_run_id="run-1", mcap_path=_VALID_MCAP
    )

    assert registration.created is False
    assert registration.robot_run == winner_run


async def test_inconsistent_canonical_state_detected() -> None:
    """RobotRun row exists but its ArtifactRecord doesn't -- must fail
    loudly, never silently repair."""
    context = _FakeContext()
    context.robot_store.runs["run-1"] = RobotRunRecord(
        run_id="run-1", robot_id="robot-1"
    )

    with pytest.raises(InconsistentCanonicalStateError):
        await register_robot_run_capture(
            context=context,
            robot_id="robot-1",
            robot_run_id="run-1",
            mcap_path=_VALID_MCAP,
        )


async def test_capture_metadata_is_stored_under_artifact_metadata() -> None:
    context = _FakeContext()

    registration = await register_robot_run_capture(
        context=context,
        robot_id="robot-1",
        robot_run_id="run-1",
        mcap_path=_VALID_MCAP,
        capture_metadata={"topic": "sceneops.robot.telemetry.v1", "partition": 0},
    )

    assert registration.artifact.metadata["capture"] == {
        "topic": "sceneops.robot.telemetry.v1",
        "partition": 0,
    }
    assert "message_count" in registration.artifact.metadata
