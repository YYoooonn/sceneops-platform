"""The verified recording resolver (ADR-007 §12.4): the one path through
which any SceneOps job consumes a registered robot recording.

    robot_run_id
      -> RobotRunRecord                    (missing -> RobotRunNotFoundError)
      -> recording ArtifactRecord          (missing / incomplete -> inconsistent
                                            canonical state)
      -> ArtifactStore.read_bytes(uri)     (any backend: local, MinIO/S3)
      -> execution-scoped local copy
      -> verify size + sha256 against the ArtifactRecord
      -> VerifiedRecording(local_path, ...) borrowed by the consumer
      -> local copy deleted on exit

A recording is consumed by identity only. No caller-supplied URI or local
path is ever an equivalent source, and the local filesystem backend is
reached through ``LocalArtifactStore`` exactly like object storage -- so
every consumer reads a private, verified copy regardless of backend.

The resolver is read-only with respect to canonical state: it never writes
RobotRunRecords, ArtifactRecords or stored recording bytes.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.robots.manifest import RecordingFormat
from sceneops_storage import ArtifactNotFoundError, ArtifactStoreError

from sceneops_worker.stores.artifacts import ArtifactRecordStore
from sceneops_worker.stores.robots import RobotStore


@dataclass(frozen=True)
class VerifiedRecording:
    """A registered recording materialized to a local file whose bytes match
    its recording ArtifactRecord. ``robot_id`` is the RobotRunRecord's -- the
    authoritative identity of the robot that produced the recording.
    ``local_path`` is borrowed: valid only
    inside the ``resolve_recording`` block, read-only, never to be moved,
    modified or deleted by the consumer."""

    robot_run_id: str
    robot_id: str
    local_path: Path
    recording_format: str
    source_clock: str
    artifact_id: str
    checksum: str
    size_bytes: int


class RecordingResolutionError(RuntimeError):
    """Base class for resolver failures. A consumer never receives a path
    after one of these."""


class RobotRunNotFoundError(RecordingResolutionError):
    """No RobotRunRecord exists for the requested robot_run_id."""


class RecordingArtifactInconsistentError(RecordingResolutionError):
    """The RobotRunRecord's recording ArtifactRecord is missing, of the wrong
    kind, or lacks its checksum/size. REGISTER_ROBOT_RUN writes both in one
    transaction, so this is inconsistent canonical state -- reported, never
    repaired."""


class UnsupportedRecordingFormatError(RecordingResolutionError):
    """The RobotRun's recording format has no supported reader."""


class RecordingBytesMissingError(RecordingResolutionError):
    """The registered ArtifactRecord exists but its bytes do not."""


class RecordingMaterializationError(RecordingResolutionError):
    """The bytes could not be read from the ArtifactStore or written to the
    local copy."""


class RecordingIntegrityError(RecordingResolutionError):
    """The materialized bytes do not match the registered ArtifactRecord's
    size or sha256 checksum."""


_SUPPORTED_FORMATS = frozenset(RecordingFormat)


@asynccontextmanager
async def resolve_recording(
    *,
    robot_run_id: str,
    robot_store: RobotStore,
    artifact_record_store: ArtifactRecordStore,
    artifact_store: ArtifactStore,
) -> AsyncIterator[VerifiedRecording]:
    """Resolve ``robot_run_id`` to a verified, execution-scoped local copy of
    its registered recording.

    The resolver owns the local copy: it is created in a fresh
    ``tempfile.TemporaryDirectory`` per call (concurrent resolutions of the
    same RobotRun never share a path) and deleted when the block exits --
    after normal completion, a consumer exception, or a verification
    failure. A killed process can leave the directory behind; it is
    disposable temp data that nothing else reads.

    Verification compares the local file -- the exact bytes the consumer
    will read -- against the ArtifactRecord, never against a backend ETag,
    a filename or the URI.
    """
    robot_run = await robot_store.get_run(robot_run_id)
    if robot_run is None:
        raise RobotRunNotFoundError(f"RobotRun not found: {robot_run_id!r}")

    if robot_run.recording_format not in _SUPPORTED_FORMATS:
        raise UnsupportedRecordingFormatError(
            f"RobotRun {robot_run_id!r} has recording format "
            f"{robot_run.recording_format!r}; supported: "
            f"{sorted(_SUPPORTED_FORMATS)}"
        )

    artifact_id = robot_run.recording_artifact_id
    artifact = await artifact_record_store.get(artifact_id)
    if artifact is None:
        raise RecordingArtifactInconsistentError(
            f"RobotRun {robot_run_id!r} references recording ArtifactRecord "
            f"{artifact_id!r}, which does not exist -- inconsistent canonical "
            f"state."
        )
    if (
        artifact.kind != ArtifactKind.ROBOT_RUN_RECORDING
        or artifact.checksum is None
        or artifact.size_bytes is None
    ):
        raise RecordingArtifactInconsistentError(
            f"Recording ArtifactRecord {artifact_id!r} of RobotRun "
            f"{robot_run_id!r} is not a verified recording artifact "
            f"(kind={artifact.kind!r}, checksum={artifact.checksum!r}, "
            f"size_bytes={artifact.size_bytes!r}) -- inconsistent canonical "
            f"state."
        )

    with tempfile.TemporaryDirectory(prefix="sceneops-recording-") as tmp_dir:
        local_path = Path(tmp_dir) / f"recording.{robot_run.recording_format}"
        await _materialize(artifact_store, artifact.uri, local_path)
        _verify(
            local_path,
            uri=artifact.uri,
            expected_size=artifact.size_bytes,
            expected_checksum=artifact.checksum,
        )
        local_path.chmod(0o444)

        yield VerifiedRecording(
            robot_run_id=robot_run_id,
            robot_id=robot_run.robot_id,
            local_path=local_path,
            recording_format=robot_run.recording_format,
            source_clock=robot_run.source_clock,
            artifact_id=artifact_id,
            checksum=artifact.checksum,
            size_bytes=artifact.size_bytes,
        )


async def _materialize(
    artifact_store: ArtifactStore, uri: str, local_path: Path
) -> None:
    # read_bytes holds the whole recording in memory; there is no streaming
    # read on the ArtifactStore contract (ADR-007 §26, deferred).
    try:
        data = await artifact_store.read_bytes(uri)
    except ArtifactNotFoundError as exc:
        raise RecordingBytesMissingError(
            f"Registered recording bytes not found at {uri}"
        ) from exc
    except (ArtifactStoreError, ValueError) as exc:
        # ValueError: a URI the configured backend cannot address.
        raise RecordingMaterializationError(
            f"Failed to read registered recording at {uri}: {exc}"
        ) from exc

    try:
        local_path.write_bytes(data)
    except OSError as exc:
        raise RecordingMaterializationError(
            f"Failed to write local copy of {uri}: {exc}"
        ) from exc


def _verify(
    local_path: Path, *, uri: str, expected_size: int, expected_checksum: str
) -> None:
    actual_size = local_path.stat().st_size
    if actual_size != expected_size:
        raise RecordingIntegrityError(
            f"Recording {uri} size mismatch: registered {expected_size} bytes, "
            f"materialized {actual_size} bytes"
        )
    with local_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    actual_checksum = f"sha256:{digest}"
    if actual_checksum != expected_checksum:
        raise RecordingIntegrityError(
            f"Recording {uri} checksum mismatch: registered {expected_checksum}, "
            f"materialized {actual_checksum}"
        )
