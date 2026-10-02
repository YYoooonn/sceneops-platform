"""REGISTER_ROBOT_RUN: the only way a RobotRunRecord comes into existence
(ADR-007 §12.1).

Input is the URI of a RobotRunManifest that the database-free Recording
Publisher (``sceneops_integrations.recording``) already wrote as its
publication marker. Registration verifies and projects; it never uploads,
copies, moves or rewrites recording or manifest bytes (I-9).

    R1  read manifest bytes
    R2  strict parse (unknown fields rejected; schema_version known)
    R3  canonical-form check: canonical(parse(bytes)) == bytes
    R4  manifest_checksum = sha256(bytes)
    R5  existing RobotRunRecord(run_id): same checksum -> no-op;
        different checksum -> hard conflict (no replacement, I-14)
    R6  verify the recording: exists, size, sha256, opens as MCAP, and its
        derived facts (time range, channels, counts) equal the manifest's
    R7  Robot platform rule (fill once, conflict fails; never last-write-wins)
    R8  one DB transaction: Robot create/fill, recording ArtifactRecord,
        manifest ArtifactRecord, RobotRunRecord; commit
    R9  unique-constraint race -> rollback, reload, resolve exactly as R5

R1-R7 failures leave no DB change. The single R8 transaction makes a
RobotRunRecord without both ArtifactRecords (or the reverse) impossible; if
such state is ever observed it is reported, never repaired.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
    ArtifactRef,
)
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.robots.manifest import (
    RobotRunManifest,
    load_canonical_robot_run_manifest,
)
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_integrations.recording import (
    RecordingValidationError,
    derive_mcap_facts,
    sha256_checksum,
)
from sceneops_storage import ArtifactNotFoundError

from sceneops_worker.core.context import WorkerContext

RECORDING_MEDIA_TYPE = "application/octet-stream"
MANIFEST_MEDIA_TYPE = "application/json"


class RobotRunRegistrationError(RuntimeError):
    """Base class for REGISTER_ROBOT_RUN failures. None of them leaves a
    canonical DB change behind."""


class PublishedArtifactMissingError(RobotRunRegistrationError):
    """The manifest, or the recording it references, does not exist."""


class RecordingVerificationError(RobotRunRegistrationError):
    """The published recording does not match its manifest (size, checksum,
    format, or derived facts)."""


class RobotRunRegistrationConflictError(RobotRunRegistrationError):
    """A RobotRunRecord already exists for this run_id with a different
    manifest checksum. RobotRuns have no replacement semantics."""


class RobotPlatformConflictError(RobotRunRegistrationError):
    """The manifest asserts a robot_platform that contradicts the Robot's
    already-set platform."""


class InconsistentCanonicalStateError(RobotRunRegistrationError):
    """Canonical DB state contradicts itself (e.g. a RobotRun artifact
    exists without its RobotRunRecord). Reported, never silently repaired."""


@dataclass(frozen=True)
class RobotRunRegistration:
    robot_run: RobotRunRecord
    recording_artifact: ArtifactRecord
    manifest_artifact: ArtifactRecord
    created: bool  # False => idempotent retry; nothing new was written


async def _read_published(context: WorkerContext, uri: str, what: str) -> bytes:
    try:
        return await context.artifact_store.read_bytes(uri)
    except (ArtifactNotFoundError, FileNotFoundError) as exc:
        raise PublishedArtifactMissingError(f"{what} not found: {uri}") from exc


async def _resolve_existing(
    context: WorkerContext, *, run_id: str, manifest_checksum: str
) -> RobotRunRegistration | None:
    """R5. Returns the existing registration for an identical manifest,
    None when nothing is registered for run_id, and raises on conflict or
    inconsistent state."""
    recording_id = robot_run_recording_artifact_id(run_id)
    manifest_id = robot_run_manifest_artifact_id(run_id)
    existing_run = await context.robot_store.get_run(run_id)

    if existing_run is None:
        orphans = [
            artifact_id
            for artifact_id in (recording_id, manifest_id)
            if await context.artifact_record_store.get(artifact_id) is not None
        ]
        if orphans:
            raise InconsistentCanonicalStateError(
                f"run_id={run_id!r} has RobotRun ArtifactRecord(s) {orphans} "
                f"but no RobotRunRecord"
            )
        return None

    if existing_run.manifest_checksum != manifest_checksum:
        raise RobotRunRegistrationConflictError(
            f"run_id={run_id!r} is already registered with a different "
            f"RobotRunManifest (existing={existing_run.manifest_checksum}, "
            f"new={manifest_checksum}); RobotRuns are never replaced"
        )

    recording = await context.artifact_record_store.get(
        existing_run.recording_artifact_id
    )
    manifest = await context.artifact_record_store.get(
        existing_run.manifest_artifact_id
    )
    if recording is None or manifest is None:
        raise InconsistentCanonicalStateError(
            f"RobotRunRecord {run_id!r} references missing ArtifactRecord(s): "
            f"recording={recording is not None} manifest={manifest is not None}"
        )
    return RobotRunRegistration(
        robot_run=existing_run,
        recording_artifact=recording,
        manifest_artifact=manifest,
        created=False,
    )


async def _verify_recording(context: WorkerContext, manifest: RobotRunManifest) -> None:
    """R6. Reads the recording through ArtifactStore and requires it to be
    exactly what the manifest describes."""
    ref = manifest.recording
    data = await _read_published(context, ref.uri, "recording")
    if len(data) != ref.size_bytes:
        raise RecordingVerificationError(
            f"recording {ref.uri} size {len(data)} != manifest size_bytes "
            f"{ref.size_bytes}"
        )
    actual_checksum = sha256_checksum(data)
    if actual_checksum != ref.checksum:
        raise RecordingVerificationError(
            f"recording {ref.uri} checksum {actual_checksum} != manifest "
            f"checksum {ref.checksum}"
        )
    try:
        facts = derive_mcap_facts(
            io.BytesIO(data), source_clock=manifest.capture.source_clock
        )
    except RecordingValidationError as exc:
        raise RecordingVerificationError(
            f"recording {ref.uri} is not a valid {ref.format.value}: {exc}"
        ) from exc
    if (
        facts.started_at != manifest.started_at
        or facts.ended_at != manifest.ended_at
        or facts.channels != manifest.channels
    ):
        raise RecordingVerificationError(
            f"recording {ref.uri} facts disagree with its manifest "
            f"(started_at/ended_at/channels)"
        )


async def _apply_robot_platform_rule(
    context: WorkerContext, manifest: RobotRunManifest
) -> None:
    """R7, inside the R8 transaction (ADR-007 §9). The Robot row is
    created if absent and then locked, so concurrent registrations for the
    same robot evaluate the fill-once rule serially."""
    await context.robot_store.create_robot_if_absent(
        RobotRecord(robot_id=manifest.robot_id, platform=manifest.robot_platform)
    )
    robot = await context.robot_store.get_robot_for_update(manifest.robot_id)
    if robot is None:  # pragma: no cover - guaranteed by create_robot_if_absent
        raise InconsistentCanonicalStateError(
            f"Robot {manifest.robot_id!r} missing after create_if_absent"
        )
    asserted = manifest.robot_platform
    if asserted is None or robot.platform == asserted:
        return
    if robot.platform is None:
        await context.robot_store.save_robot(
            robot.model_copy(update={"platform": asserted})
        )
        return
    raise RobotPlatformConflictError(
        f"robot_id={manifest.robot_id!r} has platform={robot.platform!r}; "
        f"manifest asserts robot_platform={asserted!r}. Changing a robot's "
        f"platform is an explicit Robot operation, not part of ingestion."
    )


async def register_robot_run(
    *,
    context: WorkerContext,
    manifest_uri: str,
    job_id: str | None = None,
) -> RobotRunRegistration:
    # R1-R4
    manifest_bytes = await _read_published(context, manifest_uri, "RobotRunManifest")
    manifest = load_canonical_robot_run_manifest(manifest_bytes)
    manifest_checksum = sha256_checksum(manifest_bytes)
    run_id = manifest.run_id

    # R5
    existing = await _resolve_existing(
        context, run_id=run_id, manifest_checksum=manifest_checksum
    )
    if existing is not None:
        return existing

    # R6
    await _verify_recording(context, manifest)

    # R7 + R8
    recording_id = robot_run_recording_artifact_id(run_id)
    manifest_id = robot_run_manifest_artifact_id(run_id)
    try:
        await _apply_robot_platform_rule(context, manifest)
        recording_artifact = await context.artifact_record_store.create(
            artifact_id=recording_id,
            ref=ArtifactRef(
                kind=ArtifactKind.ROBOT_RUN_RECORDING,
                uri=manifest.recording.uri,
                media_type=RECORDING_MEDIA_TYPE,
                size_bytes=manifest.recording.size_bytes,
                checksum=manifest.recording.checksum,
            ),
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=run_id,
            job_id=job_id,
        )
        manifest_artifact = await context.artifact_record_store.create(
            artifact_id=manifest_id,
            ref=ArtifactRef(
                kind=ArtifactKind.ROBOT_RUN_MANIFEST,
                uri=manifest_uri,
                media_type=MANIFEST_MEDIA_TYPE,
                size_bytes=len(manifest_bytes),
                checksum=manifest_checksum,
            ),
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=run_id,
            job_id=job_id,
        )
        robot_run = await context.robot_store.create_run(
            RobotRunRecord(
                run_id=run_id,
                robot_id=manifest.robot_id,
                started_at=manifest.started_at,
                ended_at=manifest.ended_at,
                recording_format=manifest.recording.format.value,
                source_clock=manifest.capture.source_clock,
                recording_artifact_id=recording_id,
                manifest_artifact_id=manifest_id,
                manifest_checksum=manifest_checksum,
            )
        )
        await context.commit()
    except IntegrityError:
        # R9: a concurrent registration of the same run_id committed first
        # (artifacts / robot_runs primary keys). Resolve against its result.
        await context.rollback()
        existing = await _resolve_existing(
            context, run_id=run_id, manifest_checksum=manifest_checksum
        )
        if existing is None:
            raise
        return existing
    except BaseException:
        await context.rollback()
        raise

    return RobotRunRegistration(
        robot_run=robot_run,
        recording_artifact=recording_artifact,
        manifest_artifact=manifest_artifact,
        created=True,
    )
