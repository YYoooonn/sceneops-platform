"""REGISTER_SCENES: the only writer of canonical Scene membership and of the
DatasetVersion Scene summary (ADR-007 §17, §18, I-10-I-12).

Input is a DatasetVersion and the ids of SCENE_MANIFEST ArtifactRecords
that a producer published. Registration verifies and projects; it never
writes manifest or payload bytes.

    S1  for every input artifact: the ArtifactRecord exists and is a
        SCENE_MANIFEST; its bytes match the recorded size and checksum and
        are a canonical SceneManifest (strict parse, fingerprint re-derived);
        every observation payload reference resolves to an OBSERVATION_PAYLOAD
        ArtifactRecord with exactly the referenced checksum, size and media
        type
    S2  derive each Scene's DatasetVersion-scoped id; reject duplicate units,
        mixed source kinds, and -- for recordings -- more than one RobotRun
        or producer fingerprint, an unregistered RobotRun, or a manifest
        built from different recording bytes than the RobotRun's
    S3  one transaction: lock the DatasetVersion row, then re-read the
        affected scope and apply the identity rules
          external units (per unit, §18.2)
            absent                                 -> insert
            same fingerprint and manifest checksum -> unchanged
            different, replace=False               -> conflict
            different, replace=True                -> repoint in place
          recording scope (DatasetVersion, robot_run_id), §18.3
            empty                                  -> insert the complete set
            same fingerprint                       -> unchanged (whole scope)
            different, replace=False               -> conflict
            different, replace=True                -> delete absent ids,
                                                      repoint reused ids,
                                                      insert new ids
    S4  recompute the DatasetVersion Scene summary from membership; commit

S1-S2 failures leave no DB change. Any S3 failure rolls the whole
transaction back: a registration applies its complete input or nothing,
and units are never silently skipped. Expensive reads happen before the
lock; every decision is made after it, against committed state, so
concurrent registrations into one DatasetVersion serialize and a second
registration sees the first one's result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.provenance import RecordingSegmentSource, UnitSourceKind
from sceneops_core.scenes.schemas import (
    SceneManifest,
    SceneManifestError,
    SceneRecord,
    project_scene_record,
)

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.scenes.artifacts import SceneManifestIntegrityError
from sceneops_worker.scenes.payloads import (
    PayloadIntegrityError,
    resolve_payload_artifacts,
)


class SceneRegistrationError(RuntimeError):
    """Base class. No REGISTER_SCENES failure leaves a canonical change."""


class SceneManifestRejectedError(SceneRegistrationError):
    """An input artifact is not a verifiable canonical SceneManifest."""


class SceneRegistrationScopeError(SceneRegistrationError):
    """The input set is not one well-formed registration scope."""


class SceneRegistrationConflictError(SceneRegistrationError):
    """A different revision is already registered and ``replace`` is False."""


class InconsistentSceneMembershipError(SceneRegistrationError):
    """Committed membership violates a registrar invariant. Reported, never
    repaired."""


@dataclass(frozen=True)
class VerifiedSceneManifest:
    artifact: ArtifactRecord
    manifest: SceneManifest
    record: SceneRecord


@dataclass(frozen=True)
class SceneRegistration:
    dataset_id: str
    dataset_version: str
    # The canonical members of the registered scope after commit.
    scenes: list[SceneRecord]
    created_scene_ids: list[str] = field(default_factory=list)
    replaced_scene_ids: list[str] = field(default_factory=list)
    unchanged_scene_ids: list[str] = field(default_factory=list)
    removed_scene_ids: list[str] = field(default_factory=list)


async def register_scenes(
    *,
    context: WorkerContext,
    dataset_id: str,
    dataset_version: str,
    manifest_artifact_ids: list[str],
    replace: bool = False,
) -> SceneRegistration:
    if not manifest_artifact_ids:
        raise SceneRegistrationScopeError("no scene manifests to register")
    if len(set(manifest_artifact_ids)) != len(manifest_artifact_ids):
        raise SceneRegistrationScopeError("manifest_artifact_ids contains duplicates")

    # S1-S2, outside the transaction.
    verified = [
        await _verify_manifest(
            context,
            artifact_id=artifact_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        for artifact_id in manifest_artifact_ids
    ]
    source_kind = _check_input_scope(verified)
    if source_kind == UnitSourceKind.RECORDING:
        await _verify_recording_source(context, verified)

    # S3-S4, one transaction under the DatasetVersion row lock.
    try:
        try:
            await context.dataset_store.lock_version_for_update(
                dataset_id=dataset_id, version=dataset_version
            )
        except ValueError as exc:
            raise SceneRegistrationScopeError(str(exc)) from exc

        if source_kind == UnitSourceKind.EXTERNAL:
            registration = await _apply_external(
                context, verified, dataset_id, dataset_version, replace
            )
        else:
            registration = await _apply_recording(
                context, verified, dataset_id, dataset_version, replace
            )

        summary = await context.scene_store.summarize_membership(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        await context.dataset_store.replace_scene_membership_summary(
            dataset_id=dataset_id,
            version=dataset_version,
            scene_count=summary.scene_count,
            keyframe_count=summary.keyframe_count,
            observation_count=summary.observation_count,
            observed_channels=summary.observed_channels,
        )
        await context.commit()
    except BaseException:
        await context.rollback()
        raise
    return registration


# --- S1 / S2 ---------------------------------------------------------------------


async def _verify_manifest(
    context: WorkerContext,
    *,
    artifact_id: str,
    dataset_id: str,
    dataset_version: str,
) -> VerifiedSceneManifest:
    artifact = await context.artifact_record_store.get(artifact_id)
    if artifact is None:
        raise SceneManifestRejectedError(f"manifest artifact not found: {artifact_id}")
    if artifact.kind != ArtifactKind.SCENE_MANIFEST:
        raise SceneManifestRejectedError(
            f"artifact {artifact_id} is a {artifact.kind!r}, not a canonical "
            f"{ArtifactKind.SCENE_MANIFEST.value!r}"
        )
    if not artifact.checksum or artifact.size_bytes is None:
        raise SceneManifestRejectedError(
            f"artifact {artifact_id} does not pin a checksum and size"
        )
    try:
        manifest = await context.scene_artifact_store.read_pinned_manifest(
            uri=artifact.uri, checksum=artifact.checksum, size_bytes=artifact.size_bytes
        )
    except (SceneManifestIntegrityError, SceneManifestError) as exc:
        raise SceneManifestRejectedError(f"artifact {artifact_id}: {exc}") from exc

    try:
        await resolve_payload_artifacts(
            context.artifact_record_store, (o.payload for o in manifest.observations)
        )
    except PayloadIntegrityError as exc:
        raise SceneManifestRejectedError(f"artifact {artifact_id}: {exc}") from exc
    record = project_scene_record(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        manifest=manifest,
        manifest_artifact_id=artifact.artifact_id,
        manifest_checksum=artifact.checksum,
    )
    return VerifiedSceneManifest(artifact=artifact, manifest=manifest, record=record)


def _check_input_scope(verified: list[VerifiedSceneManifest]) -> UnitSourceKind:
    scene_ids = [v.record.scene_id for v in verified]
    duplicates = sorted({s for s in scene_ids if scene_ids.count(s) > 1})
    if duplicates:
        raise SceneRegistrationScopeError(
            f"input contains more than one manifest for scene(s) {duplicates}"
        )

    kinds = {v.record.source_kind for v in verified}
    if len(kinds) != 1:
        raise SceneRegistrationScopeError(
            "one registration takes units of a single source kind, got "
            f"{sorted(k.value for k in kinds)}"
        )
    kind = kinds.pop()
    if kind == UnitSourceKind.RECORDING:
        run_ids = {v.record.robot_run_id for v in verified}
        if len(run_ids) != 1:
            raise SceneRegistrationScopeError(
                f"a recording registration covers exactly one RobotRun, got {sorted(run_ids)}"
            )
        fingerprints = {v.record.producer_fingerprint for v in verified}
        if len(fingerprints) != 1:
            raise SceneRegistrationScopeError(
                "a recording scope has exactly one producer fingerprint, got "
                f"{len(fingerprints)}"
            )
    return kind


async def _verify_recording_source(
    context: WorkerContext, verified: list[VerifiedSceneManifest]
) -> None:
    sources = [
        v.manifest.lineage.source
        for v in verified
        if isinstance(v.manifest.lineage.source, RecordingSegmentSource)
    ]
    robot_run_id = verified[0].record.robot_run_id
    run = await context.robot_store.get_run(robot_run_id)
    if run is None:
        raise SceneRegistrationScopeError(f"RobotRun is not registered: {robot_run_id}")
    recording = await context.artifact_record_store.get(run.recording_artifact_id)
    if recording is None:
        raise InconsistentSceneMembershipError(
            f"RobotRun {robot_run_id} references missing recording artifact "
            f"{run.recording_artifact_id}"
        )
    for source in sources:
        if source.recording_checksum != recording.checksum:
            raise SceneRegistrationScopeError(
                f"manifest was built from recording bytes {source.recording_checksum}, "
                f"but RobotRun {robot_run_id} is {recording.checksum}"
            )
        if source.source_clock != run.source_clock:
            raise SceneRegistrationScopeError(
                f"manifest source_clock {source.source_clock!r} differs from "
                f"RobotRun {robot_run_id} source_clock {run.source_clock!r}"
            )


# --- S3 ----------------------------------------------------------------------------


def _same_revision(existing: SceneRecord, new: SceneRecord) -> bool:
    return (
        existing.producer_fingerprint == new.producer_fingerprint
        and existing.manifest_checksum == new.manifest_checksum
    )


async def _apply_external(
    context: WorkerContext,
    verified: list[VerifiedSceneManifest],
    dataset_id: str,
    dataset_version: str,
    replace: bool,
) -> SceneRegistration:
    registration = SceneRegistration(
        dataset_id=dataset_id, dataset_version=dataset_version, scenes=[]
    )
    for item in verified:
        new = item.record
        existing = await context.scene_store.get(new.scene_id)
        if existing is None:
            registration.scenes.append(await context.scene_store.insert(new))
            registration.created_scene_ids.append(new.scene_id)
        elif _same_revision(existing, new):
            registration.scenes.append(existing)
            registration.unchanged_scene_ids.append(new.scene_id)
        elif not replace:
            raise SceneRegistrationConflictError(
                f"scene {new.scene_id} is registered at revision "
                f"{existing.manifest_checksum} (fingerprint "
                f"{existing.producer_fingerprint}); the new revision "
                f"{new.manifest_checksum} differs and replace=False"
            )
        else:
            registration.scenes.append(await context.scene_store.replace_revision(new))
            registration.replaced_scene_ids.append(new.scene_id)
    return registration


async def _apply_recording(
    context: WorkerContext,
    verified: list[VerifiedSceneManifest],
    dataset_id: str,
    dataset_version: str,
    replace: bool,
) -> SceneRegistration:
    new_records = [item.record for item in verified]
    robot_run_id = new_records[0].robot_run_id
    fingerprint = new_records[0].producer_fingerprint
    current = await context.scene_store.list_recording_scope(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        robot_run_id=robot_run_id,
    )
    current_fingerprints = {s.producer_fingerprint for s in current}
    if len(current_fingerprints) > 1:
        raise InconsistentSceneMembershipError(
            f"recording scope ({dataset_id}/{dataset_version}, {robot_run_id}) holds "
            f"{len(current_fingerprints)} producer fingerprints"
        )

    registration = SceneRegistration(
        dataset_id=dataset_id, dataset_version=dataset_version, scenes=[]
    )
    if current and current_fingerprints == {fingerprint}:
        registration.scenes.extend(current)
        registration.unchanged_scene_ids.extend(s.scene_id for s in current)
        return registration
    if current and not replace:
        raise SceneRegistrationConflictError(
            f"recording scope ({dataset_id}/{dataset_version}, {robot_run_id}) is "
            f"registered with producer fingerprint {current_fingerprints.pop()}; the "
            f"new build has {fingerprint} and replace=False"
        )

    current_by_id = {s.scene_id: s for s in current}
    new_ids = {r.scene_id for r in new_records}
    removed = sorted(set(current_by_id) - new_ids)
    await context.scene_store.delete(removed)
    registration.removed_scene_ids.extend(removed)
    for record in new_records:
        if record.scene_id in current_by_id:
            registration.scenes.append(
                await context.scene_store.replace_revision(record)
            )
            registration.replaced_scene_ids.append(record.scene_id)
        else:
            registration.scenes.append(await context.scene_store.insert(record))
            registration.created_scene_ids.append(record.scene_id)
    return registration


__all__ = [
    "InconsistentSceneMembershipError",
    "SceneManifestRejectedError",
    "SceneRegistration",
    "SceneRegistrationConflictError",
    "SceneRegistrationError",
    "SceneRegistrationScopeError",
    "VerifiedSceneManifest",
    "register_scenes",
]
