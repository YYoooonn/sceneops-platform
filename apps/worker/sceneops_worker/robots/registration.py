"""Canonical RobotRun registration: finalized local MCAP + CaptureResult
-> ArtifactStore -> ArtifactRecord -> canonical RobotRun.

The next boundary after durable capture (ros2/capture/, frozen -- this
module has no import of anything under ros2/) produces a validated,
finalized local MCAP file. This module is what turns that local file into
canonical state: an uploaded/verified object in ArtifactStore, one
ArtifactRecord, and one RobotRunRecord, registered together.

Frozen ordering, never reversed:

    validate finalized MCAP
    -> determine artifact identity/key (deterministic, see
       sceneops_core.common.ids.robot_run_recording_artifact_id)
    -> upload or verify existing object
    -> verify stored bytes
    -> register ArtifactRecord + RobotRun
    -> commit DB transaction

ArtifactStore and PostgreSQL are two separate systems, never one
transaction -- a retry after "upload succeeded, DB registration failed"
is handled by re-running this function: the deterministic artifact
key/URI means it finds and reuses the already-uploaded object (checksum
verified) rather than re-uploading or erroring.

Does not create Episodes, does not create Scenes, does not touch the
frozen canonical baseline dataset -- this boundary stops at RobotRun.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcap.reader import make_reader
from sqlalchemy.exc import IntegrityError

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
    ArtifactRef,
)
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord, RobotRunStatus

from sceneops_worker.core.context import WorkerContext


class RobotRunCaptureValidationError(ValueError):
    """The local MCAP file failed validation and must not be registered
    (missing, a ``.partial`` capture path, or unreadable/corrupt)."""


class RobotRunRegistrationConflictError(RuntimeError):
    """A canonical RobotRun or stored object already exists with content
    that conflicts with this registration attempt. Registration never
    mutates existing state in this case -- the caller must resolve the
    conflict (different robot_run_id, or investigate the mismatch)."""


class InconsistentCanonicalStateError(RuntimeError):
    """Canonical DB state contradicts itself (e.g. a RobotRun row exists
    with no corresponding ArtifactRecord, or vice versa) -- reported,
    never silently repaired. Should not occur under this module's own
    single-transaction registration, but is not assumed impossible."""


@dataclass(frozen=True)
class RobotRunCaptureRegistration:
    robot_run: RobotRunRecord
    artifact: ArtifactRecord
    created: bool  # False => idempotent retry; nothing new was written


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_local_mcap(path: Path) -> int:
    """Reject `.partial` capture paths, missing files, and invalid/corrupt
    MCAP content. Returns the message count (also rejects zero messages).

    A self-contained check, not an import from ros2/capture/validation.py
    -- ros2/capture must remain free of DB/ArtifactStore dependencies, and
    this module runs entirely on the worker side, which already depends
    on the ``mcap`` package (RosbagAdapter, apps/worker/pyproject.toml).
    """
    if ".partial" in path.parts:
        raise RobotRunCaptureValidationError(
            f"refusing to register a .partial (not-yet-finalized) capture "
            f"path: {path}"
        )
    if not path.is_file():
        raise RobotRunCaptureValidationError(f"MCAP file not found: {path}")

    message_count = 0
    try:
        with open(path, "rb") as f:
            reader = make_reader(f)
            for _schema, _channel, _message in reader.iter_messages():
                message_count += 1
    except RobotRunCaptureValidationError:
        raise
    except Exception as exc:
        raise RobotRunCaptureValidationError(
            f"MCAP file is unreadable/corrupt: {path}: {exc}"
        ) from exc

    if message_count == 0:
        raise RobotRunCaptureValidationError(
            f"MCAP file has zero messages, refusing to register: {path}"
        )
    return message_count


async def _load_existing_registration(
    *, context: WorkerContext, robot_run_id: str, artifact_id: str
) -> tuple[RobotRunRecord, ArtifactRecord] | None:
    existing_run = await context.robot_store.get_run(robot_run_id)
    existing_artifact = await context.artifact_record_store.get(artifact_id)

    if existing_run is None and existing_artifact is None:
        return None
    if existing_run is None or existing_artifact is None:
        raise InconsistentCanonicalStateError(
            f"robot_run_id={robot_run_id!r}: RobotRun and its artifact "
            f"({artifact_id!r}) disagree on existence -- "
            f"run_exists={existing_run is not None} "
            f"artifact_exists={existing_artifact is not None}"
        )
    return existing_run, existing_artifact


def _resolve_against_existing(
    existing: tuple[RobotRunRecord, ArtifactRecord],
    *,
    expected_checksum: str,
) -> RobotRunCaptureRegistration:
    existing_run, existing_artifact = existing
    if existing_artifact.checksum != expected_checksum:
        raise RobotRunRegistrationConflictError(
            f"robot_run_id={existing_run.run_id!r} is already registered "
            f"with a different checksum (existing={existing_artifact.checksum}, "
            f"new={expected_checksum}) -- refusing to mutate existing "
            f"canonical state"
        )
    return RobotRunCaptureRegistration(
        robot_run=existing_run, artifact=existing_artifact, created=False
    )


async def register_robot_run_capture(
    *,
    context: WorkerContext,
    robot_id: str,
    robot_run_id: str,
    mcap_path: Path,
    platform: str = "nuscenes-can-replay",
    capture_metadata: dict[str, Any] | None = None,
) -> RobotRunCaptureRegistration:
    """Register one finalized local MCAP as a canonical RobotRun.

    ``capture_metadata`` (optional) carries Kafka execution/provenance
    only (topic, partition, offset range, sequence range, message count)
    -- stored inside the ArtifactRecord's own generic ``metadata`` field,
    never as new DB columns; a directly `ros2 bag record`-ed file (no
    Kafka involved at all) simply omits it.
    """
    message_count = _validate_local_mcap(mcap_path)
    local_bytes = mcap_path.read_bytes()
    local_checksum = f"sha256:{_sha256_hex(local_bytes)}"
    artifact_id = robot_run_recording_artifact_id(robot_run_id)

    existing = await _load_existing_registration(
        context=context, robot_run_id=robot_run_id, artifact_id=artifact_id
    )
    if existing is not None:
        # Exact retry (checksum matches) or a same-robot_run_id conflict
        # (checksum differs) -- either way, nothing new is written here.
        return _resolve_against_existing(existing, expected_checksum=local_checksum)

    store = context.robot_run_artifact_store
    uri = store.recording_uri(robot_run_id)

    if await store.exists(robot_run_id):
        stored_bytes = await store.read_recording_bytes(robot_run_id)
        stored_checksum = f"sha256:{_sha256_hex(stored_bytes)}"
        if stored_checksum != local_checksum:
            raise RobotRunRegistrationConflictError(
                f"an object already exists at {uri} with a different "
                f"checksum (stored={stored_checksum}, local={local_checksum}) "
                f"-- refusing to overwrite"
            )
        size_bytes = len(stored_bytes)
    else:
        write_result = await store.write_recording(
            robot_run_id=robot_run_id, data=local_bytes
        )
        # Verify stored bytes immediately after upload -- never trust the
        # write call alone as proof the bytes landed correctly.
        reread = await store.read_recording_bytes(robot_run_id)
        reread_checksum = f"sha256:{_sha256_hex(reread)}"
        if reread_checksum != local_checksum:
            raise RobotRunRegistrationConflictError(
                f"stored bytes at {uri} do not match the local checksum "
                f"immediately after upload (stored={reread_checksum}, "
                f"local={local_checksum})"
            )
        size_bytes = write_result.size_bytes

    metadata: dict[str, Any] = {"message_count": message_count}
    if capture_metadata:
        metadata["capture"] = capture_metadata

    try:
        await context.robot_store.upsert_robot(
            RobotRecord(robot_id=robot_id, platform=platform)
        )
        artifact = await context.artifact_record_store.create(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.ROBOT_RUN_RECORDING,
                uri=uri,
                media_type="application/octet-stream",
                size_bytes=size_bytes,
                checksum=local_checksum,
                metadata=metadata,
            ),
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=robot_run_id,
        )
        robot_run = await context.robot_store.create_run(
            RobotRunRecord(
                run_id=robot_run_id,
                robot_id=robot_id,
                status=RobotRunStatus.COMPLETED,
                mcap_uri=uri,
            )
        )
        await context.commit()
        return RobotRunCaptureRegistration(
            robot_run=robot_run, artifact=artifact, created=True
        )
    except IntegrityError:
        # Another concurrent registration for this same robot_run_id won
        # the race between our existence check above and this write.
        # robots.robot_id / artifacts.artifact_id / robot_runs.run_id are
        # all primary keys -- this is that real DB constraint doing its
        # job, not a process-local or Redis lock.
        await context.rollback()
        existing = await _load_existing_registration(
            context=context, robot_run_id=robot_run_id, artifact_id=artifact_id
        )
        if existing is None:
            raise
        return _resolve_against_existing(existing, expected_checksum=local_checksum)
