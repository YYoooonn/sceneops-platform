"""Unit tests for REGISTER_ROBOT_RUN (sceneops_worker.robots.registration).

No Postgres: a small transactional fake DB (writes become visible only on
commit, discarded on rollback) stands in for the stores, so every failure
path can assert "no canonical state change". Published bytes live in a real
LocalArtifactStore written by the real Recording Publisher, and the real
MCAP fixture is used for fact verification.

The same invariants against real Postgres + real MinIO (transaction
atomicity, unique-constraint races, row locking) are covered in
test_registration_integration.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.robots.manifest import (
    CaptureSource,
    CaptureSourceKind,
    RobotRunManifestError,
)
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_integrations.recording import publish_recording
from sceneops_storage import LocalArtifactStore
from sceneops_worker.robots.registration import (
    InconsistentCanonicalStateError,
    PublishedArtifactMissingError,
    RecordingVerificationError,
    RobotPlatformConflictError,
    RobotRunRegistrationConflictError,
    register_robot_run,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"
_OTHER_MCAP = _FIXTURES_DIR / "nav_msgs_odometry.mcap"
_FILE_SOURCE = CaptureSource(kind=CaptureSourceKind.FILE)


class _FakeDb:
    """Committed state + pending writes of the current transaction."""

    def __init__(self) -> None:
        self.robots: dict[str, RobotRecord] = {}
        self.runs: dict[str, RobotRunRecord] = {}
        self.artifacts: dict[str, ArtifactRecord] = {}
        self._pending: list[tuple[str, str, object]] = []
        self.fail_on_create_run = False

    def view(self, table: str) -> dict:
        view = dict(getattr(self, table))
        for t, key, value in self._pending:
            if t == table:
                view[key] = value
        return view

    def stage(self, table: str, key: str, value: object, *, insert: bool) -> None:
        if insert and key in self.view(table):
            raise IntegrityError("INSERT", {}, Exception(f"duplicate {table}.{key}"))
        self._pending.append((table, key, value))

    def commit(self) -> None:
        for table, key, value in self._pending:
            getattr(self, table)[key] = value
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()


class _FakeRobotStore:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def get_run(self, run_id):
        return self._db.view("runs").get(run_id)

    async def create_run(self, run):
        if self._db.fail_on_create_run:
            raise RuntimeError("injected failure after ArtifactRecords were staged")
        self._db.stage("runs", run.run_id, run, insert=True)
        return run

    async def create_robot_if_absent(self, robot):
        if robot.robot_id not in self._db.view("robots"):
            self._db.stage("robots", robot.robot_id, robot, insert=True)

    async def get_robot_for_update(self, robot_id):
        return self._db.view("robots").get(robot_id)

    async def save_robot(self, robot):
        self._db.stage("robots", robot.robot_id, robot, insert=False)
        return robot


class _FakeArtifactRecordStore:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def get(self, artifact_id):
        return self._db.view("artifacts").get(artifact_id)

    async def create(
        self, *, artifact_id, ref, owner_type=None, owner_id=None, job_id=None, **_
    ):
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=ref.kind.value,
            uri=ref.uri,
            media_type=ref.media_type,
            size_bytes=ref.size_bytes,
            checksum=ref.checksum,
            owner_type=owner_type.value,
            owner_id=owner_id,
            job_id=job_id,
        )
        self._db.stage("artifacts", artifact_id, record, insert=True)
        return record


class _ReadOnlyStore(LocalArtifactStore):
    """Registration must never write bytes (I-9)."""

    async def write_bytes(self, uri, data):  # pragma: no cover - must not run
        raise AssertionError(f"registration wrote bytes to {uri}")

    async def write_json(self, uri, payload):  # pragma: no cover - must not run
        raise AssertionError(f"registration wrote JSON to {uri}")


class _FakeContext:
    def __init__(self, root: Path) -> None:
        self.db = _FakeDb()
        self.artifact_store = _ReadOnlyStore(root_uri=str(root))
        self.robot_store = _FakeRobotStore(self.db)
        self.artifact_record_store = _FakeArtifactRecordStore(self.db)

    async def commit(self) -> None:
        self.db.commit()

    async def rollback(self) -> None:
        self.db.rollback()

    def snapshot(self) -> tuple:
        return (dict(self.db.robots), dict(self.db.runs), dict(self.db.artifacts))


_EMPTY = ({}, {}, {})


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    path = tmp_path / "store"
    path.mkdir()
    return path


@pytest.fixture()
def context(root: Path) -> _FakeContext:
    return _FakeContext(root)


async def _publish(root: Path, mcap: Path = _VALID_MCAP, **overrides):
    kwargs = dict(
        artifact_store=LocalArtifactStore(root_uri=str(root)),
        root_uri=str(root / "robot_runs"),
        recording_path=mcap,
        run_id="run-1",
        robot_id="robot-1",
        robot_platform=None,
        capture_source=_FILE_SOURCE,
        source_clock="mcap_log_time",
    )
    kwargs.update(overrides)
    return await publish_recording(**kwargs)


# ── valid registration / idempotency / conflict ──────────────────────────────


async def test_valid_registration_creates_artifacts_and_run(root, context) -> None:
    publication = await _publish(root, robot_platform="nuscenes-can-replay")

    registration = await register_robot_run(
        context=context, manifest_uri=publication.manifest_uri, job_id="job-1"
    )

    assert registration.created is True
    run = context.db.runs["run-1"]
    assert run == registration.robot_run
    assert run.robot_id == "robot-1"
    assert run.recording_format == "mcap"
    assert run.source_clock == "mcap_log_time"
    assert run.started_at == publication.manifest.started_at
    assert run.ended_at == publication.manifest.ended_at
    assert run.manifest_checksum == publication.manifest_checksum

    recording = context.db.artifacts[run.recording_artifact_id]
    assert recording.artifact_id == "art-robotrun-run-1"
    assert recording.kind == ArtifactKind.ROBOT_RUN_RECORDING.value
    assert recording.uri == publication.recording_uri
    data = _VALID_MCAP.read_bytes()
    assert recording.checksum == f"sha256:{hashlib.sha256(data).hexdigest()}"
    assert recording.size_bytes == len(data)
    assert (recording.owner_type, recording.owner_id, recording.job_id) == (
        "robot_run",
        "run-1",
        "job-1",
    )

    manifest = context.db.artifacts[run.manifest_artifact_id]
    assert manifest.artifact_id == "art-robotrunmanifest-run-1"
    assert manifest.kind == ArtifactKind.ROBOT_RUN_MANIFEST.value
    assert manifest.uri == publication.manifest_uri
    assert manifest.checksum == publication.manifest_checksum

    assert context.db.robots["robot-1"].platform == "nuscenes-can-replay"


async def test_same_manifest_retry_is_noop(root, context) -> None:
    publication = await _publish(root)
    first = await register_robot_run(
        context=context, manifest_uri=publication.manifest_uri
    )
    before = context.snapshot()

    second = await register_robot_run(
        context=context, manifest_uri=publication.manifest_uri
    )

    assert second.created is False
    assert second.robot_run == first.robot_run
    assert context.snapshot() == before


async def test_same_run_id_different_manifest_conflicts(
    root, context, tmp_path
) -> None:
    publication = await _publish(root)
    await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    before = context.snapshot()

    other_root = tmp_path / "other"
    other_root.mkdir()
    other = await _publish(other_root, _OTHER_MCAP)
    context.artifact_store = _ReadOnlyStore(root_uri=str(tmp_path))

    with pytest.raises(RobotRunRegistrationConflictError, match="never replaced"):
        await register_robot_run(context=context, manifest_uri=other.manifest_uri)
    assert context.snapshot() == before


async def test_orphan_artifact_without_run_is_reported(root, context) -> None:
    publication = await _publish(root)
    context.db.artifacts["art-robotrun-run-1"] = ArtifactRecord(
        artifact_id="art-robotrun-run-1", kind="robot_run_recording", uri="x"
    )
    with pytest.raises(InconsistentCanonicalStateError):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    assert "run-1" not in context.db.runs


# ── verification failures leave no state ─────────────────────────────────────


async def test_missing_manifest(root, context) -> None:
    with pytest.raises(PublishedArtifactMissingError, match="RobotRunManifest"):
        await register_robot_run(
            context=context,
            manifest_uri=str(root / "nope" / "robot_run_manifest.json"),
        )
    assert context.snapshot() == _EMPTY


async def test_missing_recording(root, context) -> None:
    publication = await _publish(root)
    Path(publication.recording_uri).unlink()
    with pytest.raises(PublishedArtifactMissingError, match="recording not found"):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    assert context.snapshot() == _EMPTY


async def test_checksum_mismatch(root, context) -> None:
    publication = await _publish(root)
    data = bytearray(Path(publication.recording_uri).read_bytes())
    data[-1] ^= 0xFF  # same size, different bytes
    Path(publication.recording_uri).write_bytes(bytes(data))
    with pytest.raises(RecordingVerificationError, match="checksum"):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    assert context.snapshot() == _EMPTY


async def test_size_mismatch(root, context) -> None:
    publication = await _publish(root)
    with open(publication.recording_uri, "ab") as f:
        f.write(b"extra")
    with pytest.raises(RecordingVerificationError, match="size"):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    assert context.snapshot() == _EMPTY


async def test_manifest_facts_disagreeing_with_recording_rejected(
    root, context
) -> None:
    """A canonical manifest whose checksum/size match but whose channel
    facts were not derived from the recording is rejected."""
    publication = await _publish(root)
    manifest = publication.manifest
    tampered = manifest.model_copy(
        update={
            "channels": [
                c.model_copy(update={"message_count": c.message_count + 1})
                for c in manifest.channels
            ]
        }
    )
    Path(publication.manifest_uri).write_bytes(tampered.to_canonical_bytes())
    with pytest.raises(RecordingVerificationError, match="facts disagree"):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    assert context.snapshot() == _EMPTY


async def test_non_canonical_manifest_rejected(root, context) -> None:
    publication = await _publish(root)
    path = Path(publication.manifest_uri)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(RobotRunManifestError, match="canonical"):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    assert context.snapshot() == _EMPTY


async def test_failure_inside_transaction_rolls_back_everything(root, context) -> None:
    publication = await _publish(root, robot_platform="p")
    context.db.fail_on_create_run = True
    with pytest.raises(RuntimeError, match="injected"):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)
    # Robot, both ArtifactRecords and the RobotRun commit together or not at all.
    assert context.snapshot() == _EMPTY


# ── robot platform rule (ADR-007 §9) ─────────────────────────────────────────

_ABSENT = object()


@pytest.mark.parametrize(
    "existing,asserted,expected",
    [
        (_ABSENT, "p1", "p1"),  # absent + given -> create with that platform
        (_ABSENT, None, None),  # absent + null -> create with null platform
        (None, "p1", "p1"),  # exists with null platform + given -> fill once
        (None, None, None),  # exists with null + null -> untouched
        ("p1", "p1", "p1"),  # equal -> accept
        ("p1", None, "p1"),  # manifest null -> accept, untouched
    ],
)
async def test_platform_rule_accepts(
    root, context, existing, asserted, expected
) -> None:
    if existing is not _ABSENT:
        context.db.robots["robot-1"] = RobotRecord(
            robot_id="robot-1", platform=existing
        )
    publication = await _publish(root, robot_platform=asserted)

    await register_robot_run(context=context, manifest_uri=publication.manifest_uri)

    assert context.db.robots["robot-1"].platform == expected
    assert "run-1" in context.db.runs


async def test_platform_conflict_fails_without_state_change(root, context) -> None:
    context.db.robots["robot-1"] = RobotRecord(robot_id="robot-1", platform="p1")
    before = context.snapshot()
    publication = await _publish(root, robot_platform="p2")

    with pytest.raises(RobotPlatformConflictError):
        await register_robot_run(context=context, manifest_uri=publication.manifest_uri)

    assert context.snapshot() == before
