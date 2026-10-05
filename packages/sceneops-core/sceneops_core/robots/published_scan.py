"""Read-only scan of published RobotRun objects (ADR-008 §3.1, §5.1, §8 step
12.3).

``{robot_run_root}/{run_id}/recording.mcap`` and
``{robot_run_root}/{run_id}/robot_run_manifest.json`` are the only objects a
publication writes. This module enumerates them through
``ArtifactStore.list_objects`` and answers one question per ``run_id``: *what
publication state do the durable objects prove?* It never writes, never
consults PostgreSQL and never decides what to do about the answer.

Observation and classification are separate on purpose:

``observe_published_runs``
    I/O. Lists the root, groups objects by run prefix and reads each manifest
    object (small). Records facts only.
``verify_recording_bytes``
    I/O, optional and per run. Re-reads the recording and compares its size and
    sha256 to the manifest, because a size match from the listing cannot prove
    the bytes. The caller decides which runs justify the read.
``classify_publication``
    Pure. Facts in, one :class:`PublicationClass` plus machine-readable reasons
    out.

A valid, canonical manifest is the publication-complete marker (ADR-007 §7.2,
ADR-008 L-5): this module never infers a manifest from a recording, and a
manifest object that does not parse is not a marker.

Registration-side concepts (RobotRunRecord, Jobs) are not known here; the
reconciler correlates this module's output with them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from sceneops_core.artifacts.contracts import ArtifactObject, ArtifactStore
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.schemas import ArtifactUri
from sceneops_core.robots.manifest import (
    RobotRunManifest,
    RobotRunManifestError,
    load_canonical_robot_run_manifest,
)

RECORDING_OBJECT_NAME: Final = "recording.mcap"
MANIFEST_OBJECT_NAME: Final = "robot_run_manifest.json"

_ERROR_MESSAGE_LIMIT = 300


class PublicationClass(StrEnum):
    """What the objects under one run prefix prove about publication."""

    # Valid canonical manifest, recording present and consistent with it.
    PUBLISHED = "published"
    # Recording present, no manifest object: not published (L-5).
    RECORDING_WITHOUT_MANIFEST = "recording_without_manifest"
    # Valid manifest whose recording object is absent.
    MANIFEST_WITHOUT_RECORDING = "manifest_without_recording"
    # A manifest object exists but is not a valid canonical manifest.
    MANIFEST_MALFORMED = "manifest_malformed"
    # Valid manifest contradicted by the objects (size, checksum, identity).
    INTEGRITY_CONFLICT = "integrity_conflict"


class RecordingByteCheck(StrEnum):
    NOT_CHECKED = "not_checked"
    MATCHES = "matches"
    SIZE_MISMATCH = "size_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"


class _ScanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservedObject(_ScanModel):
    uri: ArtifactUri
    size_bytes: int
    last_modified: datetime


class ManifestFacts(_ScanModel):
    """The manifest fields reconciliation needs; the manifest itself stays the
    only source of the rest."""

    robot_id: str
    robot_platform: str | None
    recording_uri: ArtifactUri
    recording_checksum: str
    recording_size_bytes: int
    started_at: datetime
    ended_at: datetime
    source_clock: str
    channel_count: int


class PublishedRunObservation(_ScanModel):
    """Everything durable that one run prefix holds. ``run_id`` is the prefix
    name; it is the manifest's ``run_id`` only if the checks in
    :func:`classify_publication` say so."""

    run_id: str
    recording: ObservedObject | None
    manifest_object: ObservedObject | None
    # sha256 of the manifest object's bytes; None unless the manifest parsed.
    manifest_checksum: str | None
    manifest: ManifestFacts | None
    # "ErrorType: message" when the manifest object is present but invalid.
    manifest_error: str | None
    # The manifest's own run_id, when it parsed (compared to ``run_id``).
    manifest_run_id: str | None
    # Names, relative to the run prefix, of objects that are neither the
    # recording nor the manifest.
    unexpected_objects: tuple[ObservedObject, ...]
    recording_check: RecordingByteCheck
    # sha256 of the recording bytes when they were read, else None.
    recording_actual_checksum: str | None


class UnrecognizedObject(_ScanModel):
    """An object under the scan root that belongs to no run prefix with a
    recording or manifest. Reported, never classified further (ADR-008 §6,
    ``O5``)."""

    item: ObservedObject
    reason: str


class PublishedScan(_ScanModel):
    root_uri: ArtifactUri
    runs: tuple[PublishedRunObservation, ...]
    unrecognized: tuple[UnrecognizedObject, ...]


class PublicationAssessment(_ScanModel):
    classification: PublicationClass
    # Sorted, machine-readable. Empty for PUBLISHED and the plain
    # missing-object classes.
    reasons: tuple[str, ...]


def _observed(item: ArtifactObject) -> ObservedObject:
    return ObservedObject(
        uri=item.uri, size_bytes=item.size_bytes, last_modified=item.last_modified
    )


def _manifest_facts(manifest: RobotRunManifest) -> ManifestFacts:
    return ManifestFacts(
        robot_id=manifest.robot_id,
        robot_platform=manifest.robot_platform,
        recording_uri=manifest.recording.uri,
        recording_checksum=manifest.recording.checksum,
        recording_size_bytes=manifest.recording.size_bytes,
        started_at=manifest.started_at,
        ended_at=manifest.ended_at,
        source_clock=manifest.capture.source_clock,
        channel_count=len(manifest.channels),
    )


def _error_text(error: Exception) -> str:
    message = " ".join(str(error).split())
    if len(message) > _ERROR_MESSAGE_LIMIT:
        message = message[: _ERROR_MESSAGE_LIMIT - 3] + "..."
    return f"{type(error).__name__}: {message}"


def _relative_parts(root_uri: str, uri: str) -> list[str] | None:
    prefix = root_uri.rstrip("/") + "/"
    if not uri.startswith(prefix):
        return None
    return [part for part in uri[len(prefix) :].split("/") if part]


async def observe_published_runs(
    store: ArtifactStore, root_uri: ArtifactUri
) -> PublishedScan:
    """List ``root_uri`` and observe every run prefix holding a recording or a
    manifest object. Reads each manifest object; reads no recording. A store
    error (including an object that vanishes between the listing and its read)
    propagates: a scan that cannot see the facts must not guess them."""
    objects = await store.list_objects(root_uri)

    by_run: dict[str, dict[str, ArtifactObject]] = {}
    unexpected: dict[str, list[tuple[str, ArtifactObject]]] = {}
    stray: list[UnrecognizedObject] = []
    for item in objects:
        parts = _relative_parts(root_uri, item.uri)
        if not parts:
            stray.append(
                UnrecognizedObject(item=_observed(item), reason="outside_scan_root")
            )
        elif len(parts) == 1:
            stray.append(
                UnrecognizedObject(item=_observed(item), reason="not_under_run_prefix")
            )
        else:
            run_id, name = parts[0], "/".join(parts[1:])
            if name in (RECORDING_OBJECT_NAME, MANIFEST_OBJECT_NAME):
                by_run.setdefault(run_id, {})[name] = item
            else:
                unexpected.setdefault(run_id, []).append((name, item))

    runs: list[PublishedRunObservation] = []
    for run_id in sorted(by_run):
        named = by_run[run_id]
        manifest_item = named.get(MANIFEST_OBJECT_NAME)
        recording_item = named.get(RECORDING_OBJECT_NAME)

        manifest_checksum = manifest = manifest_run_id = manifest_error = None
        if manifest_item is not None:
            data = await store.read_bytes(manifest_item.uri)
            try:
                parsed = load_canonical_robot_run_manifest(data)
            except RobotRunManifestError as exc:
                manifest_error = _error_text(exc)
            else:
                manifest = _manifest_facts(parsed)
                manifest_run_id = parsed.run_id
                manifest_checksum = sha256_checksum(data)

        runs.append(
            PublishedRunObservation(
                run_id=run_id,
                recording=_observed(recording_item) if recording_item else None,
                manifest_object=_observed(manifest_item) if manifest_item else None,
                manifest_checksum=manifest_checksum,
                manifest=manifest,
                manifest_error=manifest_error,
                manifest_run_id=manifest_run_id,
                unexpected_objects=tuple(
                    _observed(item) for _, item in sorted(unexpected.get(run_id, []))
                ),
                recording_check=RecordingByteCheck.NOT_CHECKED,
                recording_actual_checksum=None,
            )
        )

    # Prefixes that hold neither a recording nor a manifest are not runs.
    for run_id in sorted(set(unexpected) - set(by_run)):
        for _, item in sorted(unexpected[run_id]):
            stray.append(
                UnrecognizedObject(
                    item=_observed(item), reason="no_recording_or_manifest"
                )
            )

    return PublishedScan(
        root_uri=root_uri,
        runs=tuple(runs),
        unrecognized=tuple(sorted(stray, key=lambda entry: entry.item.uri)),
    )


async def verify_recording_bytes(
    store: ArtifactStore, observation: PublishedRunObservation
) -> PublishedRunObservation:
    """Return ``observation`` with its recording bytes compared to the
    manifest. Only meaningful for a run with a valid manifest and a recording
    object; any other observation is returned unchanged."""
    if observation.manifest is None or observation.recording is None:
        return observation

    data = await store.read_bytes(observation.recording.uri)
    actual = sha256_checksum(data)
    if len(data) != observation.manifest.recording_size_bytes:
        check = RecordingByteCheck.SIZE_MISMATCH
    elif actual != observation.manifest.recording_checksum:
        check = RecordingByteCheck.CHECKSUM_MISMATCH
    else:
        check = RecordingByteCheck.MATCHES
    return observation.model_copy(
        update={"recording_check": check, "recording_actual_checksum": actual}
    )


def classify_publication(observation: PublishedRunObservation) -> PublicationAssessment:
    """Pure classification of one run prefix. The first matching rule wins."""
    if observation.manifest_object is None:
        return PublicationAssessment(
            classification=PublicationClass.RECORDING_WITHOUT_MANIFEST, reasons=()
        )

    if observation.manifest is None:
        return PublicationAssessment(
            classification=PublicationClass.MANIFEST_MALFORMED,
            reasons=(observation.manifest_error or "manifest_invalid",),
        )

    manifest = observation.manifest
    reasons: list[str] = []
    if observation.manifest_run_id != observation.run_id:
        reasons.append("manifest_run_id_mismatch")
    expected_recording_uri = (
        observation.manifest_object.uri.rsplit("/", 1)[0] + "/" + RECORDING_OBJECT_NAME
    )
    if manifest.recording_uri != expected_recording_uri:
        reasons.append("manifest_recording_uri_mismatch")
    if (
        observation.recording is not None
        and observation.recording.size_bytes != manifest.recording_size_bytes
    ):
        reasons.append("recording_size_mismatch")
    if observation.recording_check == RecordingByteCheck.SIZE_MISMATCH:
        reasons.append("recording_size_mismatch")
    if observation.recording_check == RecordingByteCheck.CHECKSUM_MISMATCH:
        reasons.append("recording_checksum_mismatch")
    if reasons:
        return PublicationAssessment(
            classification=PublicationClass.INTEGRITY_CONFLICT,
            reasons=tuple(sorted(set(reasons))),
        )

    if observation.recording is None:
        return PublicationAssessment(
            classification=PublicationClass.MANIFEST_WITHOUT_RECORDING, reasons=()
        )
    return PublicationAssessment(classification=PublicationClass.PUBLISHED, reasons=())


__all__ = [
    "MANIFEST_OBJECT_NAME",
    "RECORDING_OBJECT_NAME",
    "ManifestFacts",
    "ObservedObject",
    "PublicationAssessment",
    "PublicationClass",
    "PublishedRunObservation",
    "PublishedScan",
    "RecordingByteCheck",
    "UnrecognizedObject",
    "classify_publication",
    "observe_published_runs",
    "verify_recording_bytes",
]
