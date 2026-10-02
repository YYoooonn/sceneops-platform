"""Unit tests for the verified recording resolver
(sceneops_worker.robots.resolver) -- no database, no MinIO.

RobotRunRecord / ArtifactRecord lookups are fakes. The local backend is a
real ``LocalArtifactStore``; object storage is a fake ``ArtifactStore`` keyed
by ``s3://`` URI, so both backends go through the same resolver path. Real
Postgres + MinIO coverage lives in test_resolver_integration.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_storage import ArtifactNotFoundError, ArtifactReadError
from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_worker.robots.resolver import (
    RecordingArtifactInconsistentError,
    RecordingBytesMissingError,
    RecordingIntegrityError,
    RecordingMaterializationError,
    RobotRunNotFoundError,
    UnsupportedRecordingFormatError,
    VerifiedRecording,
    resolve_recording,
)

_RUN_ID = "run-1"
_ARTIFACT_ID = "art-robotrun-run-1"
_DATA = b"\x89MCAP0\r\n" + b"recording-bytes" * 64


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class _FakeRobotStore:
    def __init__(self, runs: list[RobotRunRecord]) -> None:
        self._runs = {run.run_id: run for run in runs}
        self.calls: list[str] = []

    async def get_run(self, run_id: str) -> RobotRunRecord | None:
        self.calls.append(f"get_run:{run_id}")
        return self._runs.get(run_id)


class _FakeArtifactRecordStore:
    def __init__(self, artifacts: list[ArtifactRecord]) -> None:
        self._artifacts = {a.artifact_id: a for a in artifacts}
        self.calls: list[str] = []

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        self.calls.append(f"get:{artifact_id}")
        return self._artifacts.get(artifact_id)


class _FakeObjectStore:
    """read_bytes-only stand-in for an S3/MinIO-backed ArtifactStore. Any
    other method call fails the test -- the resolver must never write."""

    def __init__(self, objects: dict[str, bytes], *, error: Exception | None = None):
        self.objects = objects
        self.error = error
        self.read_calls: list[str] = []

    async def read_bytes(self, uri: str) -> bytes:
        self.read_calls.append(uri)
        if self.error is not None:
            raise self.error
        if uri not in self.objects:
            raise ArtifactNotFoundError(uri)
        return self.objects[uri]


def _robot_run(*, recording_format: str = "mcap") -> RobotRunRecord:
    return RobotRunRecord(
        run_id=_RUN_ID,
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format=recording_format,
        source_clock="mcap_log_time",
        recording_artifact_id=_ARTIFACT_ID,
        manifest_artifact_id="art-robotrunmanifest-run-1",
        manifest_checksum="sha256:" + "1" * 64,
    )


def _artifact(uri: str, data: bytes = _DATA, **overrides) -> ArtifactRecord:
    fields = {
        "artifact_id": _ARTIFACT_ID,
        "kind": ArtifactKind.ROBOT_RUN_RECORDING,
        "uri": uri,
        "checksum": _sha256(data),
        "size_bytes": len(data),
    }
    fields.update(overrides)
    return ArtifactRecord(**fields)


@pytest.fixture()
def isolated_tmp(tmp_path, monkeypatch) -> Path:
    """Points ``tempfile`` at an empty directory so a test can assert that
    the resolver left nothing behind."""
    root = tmp_path / "os-tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture()
async def local_backend(tmp_path):
    store = LocalArtifactStore(root_uri=str(tmp_path / "artifacts"))
    uri = store.join_uri(store.root_uri, "robot_runs", _RUN_ID, "recording.mcap")
    await store.write_bytes(uri, _DATA)
    return store, uri


def _resolve(robot_store, artifact_record_store, artifact_store, run_id=_RUN_ID):
    return resolve_recording(
        robot_run_id=run_id,
        robot_store=robot_store,
        artifact_record_store=artifact_record_store,
        artifact_store=artifact_store,
    )


# ── successful resolution ──────────────────────────────────────────────────


async def test_local_backend_yields_verified_private_copy(
    local_backend, isolated_tmp
) -> None:
    store, uri = local_backend
    artifact = _artifact(uri)

    async with _resolve(
        _FakeRobotStore([_robot_run()]), _FakeArtifactRecordStore([artifact]), store
    ) as recording:
        assert recording == VerifiedRecording(
            robot_run_id=_RUN_ID,
            robot_id="robot-1",
            local_path=recording.local_path,
            recording_format="mcap",
            source_clock="mcap_log_time",
            artifact_id=_ARTIFACT_ID,
            checksum=_sha256(_DATA),
            size_bytes=len(_DATA),
        )
        assert recording.local_path.read_bytes() == _DATA
        # A private copy under the resolver's temp dir, never the stored
        # artifact itself, and read-only for the borrowing consumer.
        assert recording.local_path != Path(uri)
        assert recording.local_path.is_relative_to(isolated_tmp)
        assert not os.stat(recording.local_path).st_mode & stat.S_IWUSR
        local_path = recording.local_path

    assert not local_path.exists()
    assert list(isolated_tmp.iterdir()) == []
    # The stored artifact is untouched.
    assert Path(uri).read_bytes() == _DATA


async def test_object_store_backend_yields_verified_copy(isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    store = _FakeObjectStore({uri: _DATA})

    async with _resolve(
        _FakeRobotStore([_robot_run()]),
        _FakeArtifactRecordStore([_artifact(uri)]),
        store,
    ) as recording:
        assert recording.local_path.read_bytes() == _DATA

    assert store.read_calls == [uri]
    assert list(isolated_tmp.iterdir()) == []


async def test_lookup_is_read_only_and_keyed_by_identity(local_backend) -> None:
    store, uri = local_backend
    robot_store = _FakeRobotStore([_robot_run()])
    artifact_record_store = _FakeArtifactRecordStore([_artifact(uri)])

    async with _resolve(robot_store, artifact_record_store, store):
        pass

    # Only lookups -- the fakes have no write methods at all, so any
    # attempted write would have raised AttributeError.
    assert robot_store.calls == [f"get_run:{_RUN_ID}"]
    assert artifact_record_store.calls == [f"get:{_ARTIFACT_ID}"]


# ── lookup failures ────────────────────────────────────────────────────────


async def test_missing_robot_run_fails() -> None:
    store = _FakeObjectStore({})
    with pytest.raises(RobotRunNotFoundError, match="run-unknown"):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([]),
            store,
            run_id="run-unknown",
        ):
            pytest.fail("must not yield")
    assert store.read_calls == []


async def test_missing_artifact_record_fails() -> None:
    store = _FakeObjectStore({})
    with pytest.raises(RecordingArtifactInconsistentError, match="does not exist"):
        async with _resolve(
            _FakeRobotStore([_robot_run()]), _FakeArtifactRecordStore([]), store
        ):
            pytest.fail("must not yield")
    assert store.read_calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": ArtifactKind.EPISODE_MANIFEST},
        {"checksum": None},
        {"size_bytes": None},
    ],
)
async def test_incomplete_artifact_record_fails(overrides) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    store = _FakeObjectStore({uri: _DATA})
    with pytest.raises(RecordingArtifactInconsistentError):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri, **overrides)]),
            store,
        ):
            pytest.fail("must not yield")
    assert store.read_calls == []


async def test_unsupported_recording_format_fails() -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.bag"
    store = _FakeObjectStore({uri: _DATA})
    with pytest.raises(UnsupportedRecordingFormatError, match="rosbag1"):
        async with _resolve(
            _FakeRobotStore([_robot_run(recording_format="rosbag1")]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            store,
        ):
            pytest.fail("must not yield")
    assert store.read_calls == []


# ── materialization failures ───────────────────────────────────────────────


async def test_missing_object_fails(isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    with pytest.raises(RecordingBytesMissingError):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            _FakeObjectStore({}),
        ):
            pytest.fail("must not yield")
    assert list(isolated_tmp.iterdir()) == []


async def test_missing_local_file_fails(local_backend, isolated_tmp) -> None:
    store, uri = local_backend
    Path(uri).unlink()
    with pytest.raises(RecordingBytesMissingError):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            store,
        ):
            pytest.fail("must not yield")
    assert list(isolated_tmp.iterdir()) == []


@pytest.mark.parametrize(
    "error", [ArtifactReadError("connection reset"), ValueError("bad scheme")]
)
async def test_storage_read_failure_fails(error, isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    with pytest.raises(RecordingMaterializationError):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            _FakeObjectStore({uri: _DATA}, error=error),
        ):
            pytest.fail("must not yield")
    assert list(isolated_tmp.iterdir()) == []


# ── integrity failures (cleanup after verification failure) ───────────────


async def test_size_mismatch_fails_and_cleans_up(isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    with pytest.raises(RecordingIntegrityError, match="size mismatch"):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            _FakeObjectStore({uri: _DATA + b"trailing"}),
        ):
            pytest.fail("must not yield")
    assert list(isolated_tmp.iterdir()) == []


async def test_checksum_mismatch_fails_and_cleans_up(isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    tampered = _DATA[:-1] + bytes([_DATA[-1] ^ 0xFF])  # same size
    with pytest.raises(RecordingIntegrityError, match="checksum mismatch"):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            _FakeObjectStore({uri: tampered}),
        ):
            pytest.fail("must not yield")
    assert list(isolated_tmp.iterdir()) == []


# ── cleanup after the consumer's own block ─────────────────────────────────


async def test_cleanup_after_consumer_exception(isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"

    class _ReaderFailure(Exception):
        pass

    paths: list[Path] = []
    with pytest.raises(_ReaderFailure):
        async with _resolve(
            _FakeRobotStore([_robot_run()]),
            _FakeArtifactRecordStore([_artifact(uri)]),
            _FakeObjectStore({uri: _DATA}),
        ) as recording:
            paths.append(recording.local_path)
            raise _ReaderFailure("RosbagAdapter failed mid-parse")

    assert not paths[0].exists()
    assert list(isolated_tmp.iterdir()) == []


async def test_concurrent_resolutions_get_independent_copies(isolated_tmp) -> None:
    uri = "s3://sceneops/robot_runs/run-1/recording.mcap"
    robot_store = _FakeRobotStore([_robot_run()])
    artifact_record_store = _FakeArtifactRecordStore([_artifact(uri)])
    store = _FakeObjectStore({uri: _DATA})
    barrier = asyncio.Barrier(2)

    async def _consume() -> Path:
        async with _resolve(robot_store, artifact_record_store, store) as recording:
            await barrier.wait()  # both copies exist at the same time
            assert recording.local_path.read_bytes() == _DATA
            return recording.local_path

    path_a, path_b = await asyncio.gather(_consume(), _consume())

    assert path_a.parent != path_b.parent
    assert list(isolated_tmp.iterdir()) == []
