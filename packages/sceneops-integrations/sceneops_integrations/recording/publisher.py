"""Database-free Recording Publisher (ADR-007 §7.2).

Turns one finalized local MCAP plus normalized publication inputs into two
durable objects under a deterministic per-run prefix::

    {root_uri}/{run_id}/recording.mcap            (P3, write-once)
    {root_uri}/{run_id}/robot_run_manifest.json   (P5, written LAST)

The manifest is the publication marker: it is written only after the
recording was uploaded and its stored bytes re-read and verified, so a crash
anywhere before P5 leaves no manifest and nothing is considered published.
Orphaned recording bytes are harmless and are reused by a retry.

Ownership: this module writes physical bytes only. It never opens a DB
session, never imports ``sceneops-db``, and never creates ArtifactRecords --
``REGISTER_ROBOT_RUN`` verifies what was published here and registers it.

Write-once enforcement and its residual race: ArtifactStore offers no
conditional create, so each write is "check existence, then write, then
re-read and verify". Matching existing bytes are reused (idempotent retry);
differing bytes are a hard conflict and are never overwritten. Two
publishers racing on the same ``run_id`` with different bytes can still both
pass the existence check; the loser's post-write verification, registration
(which re-verifies checksum and size against the manifest) and every
consumer's materialization checksum then detect the mismatch and fail
loudly. Capture routing by ``robot_run_id`` provides the single-publisher-
per-run assumption this relies on.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.robots.manifest import (
    RUN_ID_MAX_LENGTH,
    CaptureInfo,
    CaptureSource,
    RecordingFormat,
    RecordingRef,
    RobotRunManifest,
    validate_identifier,
)

from .facts import RecordingValidationError, derive_mcap_facts, sha256_checksum

RECORDING_OBJECT_NAME = "recording.mcap"
MANIFEST_OBJECT_NAME = "robot_run_manifest.json"


class RecordingPublicationConflictError(RuntimeError):
    """An object already exists at a write-once key with different bytes."""


class RecordingPublicationIntegrityError(RuntimeError):
    """Bytes read back right after a write do not match what was written."""


@dataclass(frozen=True)
class RecordingPublication:
    manifest: RobotRunManifest
    manifest_uri: str
    manifest_checksum: str
    recording_uri: str
    # False => the object already existed with identical bytes (retry).
    recording_written: bool
    manifest_written: bool


def recording_uri(store: ArtifactStore, root_uri: str, run_id: str) -> str:
    return store.join_uri(root_uri, run_id, RECORDING_OBJECT_NAME)


def manifest_uri(store: ArtifactStore, root_uri: str, run_id: str) -> str:
    return store.join_uri(root_uri, run_id, MANIFEST_OBJECT_NAME)


async def _write_once(store: ArtifactStore, uri: str, data: bytes) -> bool:
    """Returns True if the bytes were written, False if identical bytes
    were already present."""
    expected = sha256_checksum(data)
    if await store.exists(uri):
        existing = await store.read_bytes(uri)
        if len(existing) != len(data) or sha256_checksum(existing) != expected:
            raise RecordingPublicationConflictError(
                f"{uri} already exists with different content "
                f"(existing={sha256_checksum(existing)} size={len(existing)}, "
                f"new={expected} size={len(data)}); refusing to overwrite"
            )
        return False

    await store.write_bytes(uri, data)
    stored = await store.read_bytes(uri)
    if len(stored) != len(data) or sha256_checksum(stored) != expected:
        raise RecordingPublicationIntegrityError(
            f"bytes read back from {uri} do not match what was written "
            f"(stored={sha256_checksum(stored)} size={len(stored)}, "
            f"written={expected} size={len(data)})"
        )
    return True


def _read_finalized_recording(path: Path) -> bytes:
    if path.suffix == ".partial" or ".partial" in path.parts:
        raise RecordingValidationError(
            f"refusing to publish a .partial (not finalized) capture path: {path}"
        )
    if not path.is_file():
        raise RecordingValidationError(f"recording file not found: {path}")
    return path.read_bytes()


async def publish_recording(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    recording_path: Path,
    run_id: str,
    robot_id: str,
    robot_platform: str | None,
    capture_source: CaptureSource,
    source_clock: str,
) -> RecordingPublication:
    """Publish one finalized MCAP (P1-P5). Idempotent for identical inputs:
    a retry reuses both objects and returns the same manifest bytes."""
    validate_identifier(run_id, field="run_id", max_length=RUN_ID_MAX_LENGTH)

    # P1 -- validate the local recording and derive its facts.
    data = _read_finalized_recording(recording_path)
    facts = derive_mcap_facts(io.BytesIO(data), source_clock=source_clock)

    # P2 -- deterministic write-once keys.
    target_recording_uri = recording_uri(artifact_store, root_uri, run_id)
    target_manifest_uri = manifest_uri(artifact_store, root_uri, run_id)

    # P3 -- publish and verify the recording.
    recording_written = await _write_once(artifact_store, target_recording_uri, data)

    # P4 -- canonical manifest bytes.
    manifest = RobotRunManifest(
        run_id=run_id,
        robot_id=robot_id,
        robot_platform=robot_platform,
        started_at=facts.started_at,
        ended_at=facts.ended_at,
        recording=RecordingRef(
            format=RecordingFormat.MCAP,
            uri=target_recording_uri,
            checksum=sha256_checksum(data),
            size_bytes=len(data),
        ),
        capture=CaptureInfo(source=capture_source, source_clock=source_clock),
        channels=facts.channels,
    )
    manifest_bytes = manifest.to_canonical_bytes()

    # P5 -- the manifest is written last: it is the publication marker.
    manifest_written = await _write_once(
        artifact_store, target_manifest_uri, manifest_bytes
    )

    return RecordingPublication(
        manifest=manifest,
        manifest_uri=target_manifest_uri,
        manifest_checksum=sha256_checksum(manifest_bytes),
        recording_uri=target_recording_uri,
        recording_written=recording_written,
        manifest_written=manifest_written,
    )
