"""REGISTER_SCENES: the only writer of canonical Scene units and revisions
(ADR-015 §6.4, MS-7).

Input is the ids of the SCENE_MANIFEST ArtifactRecords of one build (one
source, one producer fingerprint). Registration verifies, then adds
immutable revisions; it never writes manifest or payload bytes, never reads
a SourceSnapshot, and never reads or writes a DatasetVersion: membership is
a separate composition that pins the revisions registration returns.

    S1  durability and integrity gate, for every input artifact, against the
        ArtifactStore itself and not only against database rows:
          manifest  the ArtifactRecord exists, is a SCENE_MANIFEST and pins
                    a checksum and size; its bytes are read back and match
                    both, and are a canonical SceneManifest (strict parse,
                    fingerprint re-derived); its id is the revision's
                    deterministic manifest artifact id
          payloads  by reference kind (the only kind dispatch, MS-10):
                    an ArtifactPayloadRef resolves to an OBSERVATION_PAYLOAD
                    ArtifactRecord with exactly the referenced checksum, size
                    and media type, whose object is present in the store with
                    its recorded size (one listing per payload prefix); a
                    SourceFileRef is checked in S2 against its content
    S2  derive each Scene's unit id; reject duplicate units, more than one
        source or producer fingerprint, and a source binding that is not the
        registered source:
          recording  the RobotRun is registered and the manifests were built
                     from its recording bytes
          content    the SourceContent is registered with the pinned
                     content_checksum, and every SourceFileRef matches its
                     inventory entry (path, sha256, size) and is stored with
                     that size (one listing per content prefix)
    S3  one transaction: register each unit and revision additively
        (sceneops_db.repositories.revisions); commit
          same unit + same manifest checksum          -> no-op
          new unit, or a new producer fingerprint     -> insert
          same unit + same fingerprint + other checksum
                                                      -> conflict (a
                                                         non-deterministic
                                                         producer)

The result is the complete pin of every input revision, all of which are
registered once the call returns. S1-S2 failures leave no DB change, so no
revision row exists for bytes that were not verified. Any S3 failure rolls
the whole transaction back: a registration applies its complete input or
nothing. No lock is taken: the unique keys of units and revisions decide
concurrent registrations, which converge when identical and fail when they
conflict; units are inserted in unit id order so overlapping registrations
cannot deadlock. A retry after a crash, before or after commit,
re-verifies and converges on the committed rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactPayloadRef,
    ArtifactRecord,
    SourceFileRef,
)
from sceneops_core.provenance import (
    InconsistentRevisionsError,
    NonDeterministicProducerError,
    RevisionDecision,
    RevisionPin,
)
from sceneops_core.scenes.schemas import (
    SceneManifest,
    SceneManifestError,
    SceneRevisionRecord,
    project_scene_revision,
)
from sceneops_db.repositories import UnitIdentityConflictError

from sceneops_worker.core.context import WorkerContext
from sceneops_scenes.artifacts import SceneManifestIntegrityError
from sceneops_worker.recordings.payload_refs import (
    PayloadIntegrityError,
    resolve_payload_artifacts,
    verify_payload_objects,
)
from sceneops_worker.sources.content import (
    RegisteredContentError,
    SourceFileRefError,
    resolve_registered_content,
    verify_source_file_refs,
)


class SceneRegistrationError(RuntimeError):
    """Base class. No REGISTER_SCENES failure leaves a canonical change."""


class SceneManifestRejectedError(SceneRegistrationError):
    """An input artifact, or an object it references, failed the durability
    and integrity gate."""


class SceneRegistrationScopeError(SceneRegistrationError):
    """The input set is not the output of one build of one registered
    source (a RobotRun or a SourceContent)."""


class SceneRegistrationConflictError(SceneRegistrationError):
    """A unit already has another revision under the same producer
    fingerprint (a non-deterministic producer)."""


class InconsistentSceneRegistrationError(SceneRegistrationError):
    """Committed units or revisions violate a registrar invariant. Reported,
    never repaired."""


@dataclass(frozen=True)
class VerifiedSceneManifest:
    artifact: ArtifactRecord
    manifest: SceneManifest
    record: SceneRevisionRecord


@dataclass(frozen=True)
class RegisteredSceneRevision:
    decision: RevisionDecision
    revision: SceneRevisionRecord

    @property
    def pin(self) -> RevisionPin:
        return self.revision.pin()


@dataclass(frozen=True)
class SceneRegistration:
    source_kind: str
    source_id: str
    producer_fingerprint: str
    # One per input manifest, in unit id order.
    revisions: list[RegisteredSceneRevision]

    @property
    def pins(self) -> list[RevisionPin]:
        return [r.pin for r in self.revisions]

    def scene_ids(self, decision: RevisionDecision | None = None) -> list[str]:
        return [
            r.revision.scene_id
            for r in self.revisions
            if decision is None or r.decision == decision
        ]


async def register_scenes(
    *, context: WorkerContext, manifest_artifact_ids: list[str]
) -> SceneRegistration:
    if not manifest_artifact_ids:
        raise SceneRegistrationScopeError("no scene manifests to register")
    if len(set(manifest_artifact_ids)) != len(manifest_artifact_ids):
        raise SceneRegistrationScopeError("manifest_artifact_ids contains duplicates")

    # S1-S2, outside the transaction.
    verified = [
        await _verify_manifest(context, artifact_id=artifact_id)
        for artifact_id in manifest_artifact_ids
    ]
    _check_input_scope(verified)
    if verified[0].record.source_kind == "recording":
        await _verify_recording_source(context, verified)
    else:
        await _verify_content_source(context, verified)

    # S3, one transaction. Inserts go in unit id order: an insert waits for a
    # concurrent uncommitted insert of the same key, so two registrations of
    # overlapping units in different input orders would otherwise deadlock.
    try:
        registered = [
            await _register(context, item)
            for item in sorted(verified, key=lambda v: v.record.scene_id)
        ]
        await context.commit()
    except BaseException:
        await context.rollback()
        raise
    return SceneRegistration(
        source_kind=verified[0].record.source_kind,
        source_id=verified[0].record.source_id,
        producer_fingerprint=verified[0].record.producer_fingerprint,
        revisions=registered,
    )


# --- S1 / S2 ---------------------------------------------------------------------


async def _verify_manifest(
    context: WorkerContext, *, artifact_id: str
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
        payloads = await resolve_payload_artifacts(
            context.artifact_record_store,
            (
                o.payload
                for o in manifest.observations
                if isinstance(o.payload, ArtifactPayloadRef)
            ),
        )
        await verify_payload_objects(context.artifact_store, payloads.values())
    except PayloadIntegrityError as exc:
        raise SceneManifestRejectedError(f"artifact {artifact_id}: {exc}") from exc
    try:
        record = project_scene_revision(
            manifest=manifest,
            manifest_artifact_id=artifact.artifact_id,
            manifest_checksum=artifact.checksum,
        )
    except ValueError as exc:
        # e.g. an artifact id that is not the revision's deterministic id.
        raise SceneManifestRejectedError(f"artifact {artifact_id}: {exc}") from exc
    return VerifiedSceneManifest(artifact=artifact, manifest=manifest, record=record)


def _check_input_scope(verified: list[VerifiedSceneManifest]) -> None:
    scene_ids = [v.record.scene_id for v in verified]
    duplicates = sorted({s for s in scene_ids if scene_ids.count(s) > 1})
    if duplicates:
        raise SceneRegistrationScopeError(
            f"input contains more than one manifest for scene(s) {duplicates}"
        )

    sources = {(v.record.source_kind, v.record.source_id) for v in verified}
    if len(sources) != 1:
        raise SceneRegistrationScopeError(
            f"a registration covers exactly one source, got {sorted(sources)}"
        )
    fingerprints = {v.record.producer_fingerprint for v in verified}
    if len(fingerprints) != 1:
        raise SceneRegistrationScopeError(
            "a registration covers one build with one producer fingerprint, got "
            f"{len(fingerprints)}"
        )


async def _verify_recording_source(
    context: WorkerContext, verified: list[VerifiedSceneManifest]
) -> None:
    """The manifests must have been built from the RobotRun's registered
    recording bytes. Their segment clock is the producer's declared
    segmentation clock (Q4), not necessarily the recording clock, so it is
    not compared with the RobotRun."""
    robot_run_id = verified[0].record.source_id
    run = await context.robot_store.get_run(robot_run_id)
    if run is None:
        raise SceneRegistrationScopeError(f"RobotRun is not registered: {robot_run_id}")
    recording = await context.artifact_record_store.get(run.recording_artifact_id)
    if recording is None:
        raise InconsistentSceneRegistrationError(
            f"RobotRun {robot_run_id} references missing recording artifact "
            f"{run.recording_artifact_id}"
        )
    for item in verified:
        source = item.manifest.lineage.source
        if source.recording_checksum != recording.checksum:
            raise SceneRegistrationScopeError(
                f"manifest was built from recording bytes {source.recording_checksum}, "
                f"but RobotRun {robot_run_id} is {recording.checksum}"
            )


async def _verify_content_source(
    context: WorkerContext, verified: list[VerifiedSceneManifest]
) -> None:
    """The manifests must have been built from the registered SourceContent's
    bytes, and every preserved file they reference must be that content's,
    as its inventory pins it and as the store holds it."""
    content_id = verified[0].record.source_id
    try:
        content = await resolve_registered_content(context, content_id)
    except RegisteredContentError as exc:
        raise SceneRegistrationScopeError(str(exc)) from exc
    for item in verified:
        source = item.manifest.lineage.source
        if source.content_checksum != content.record.content_checksum:
            raise SceneRegistrationScopeError(
                f"manifest was built from content bytes {source.content_checksum}, "
                f"but SourceContent {content_id} is {content.record.content_checksum}"
            )
    try:
        await verify_source_file_refs(
            context,
            content,
            (
                o.payload
                for item in verified
                for o in item.manifest.observations
                if isinstance(o.payload, SourceFileRef)
            ),
        )
    except SourceFileRefError as exc:
        raise SceneManifestRejectedError(str(exc)) from exc


# --- S3 ----------------------------------------------------------------------------


async def _register(
    context: WorkerContext, item: VerifiedSceneManifest
) -> RegisteredSceneRevision:
    try:
        registered = await context.scene_store.register_revision(item.record)
    except NonDeterministicProducerError as exc:
        raise SceneRegistrationConflictError(str(exc)) from exc
    except (InconsistentRevisionsError, UnitIdentityConflictError) as exc:
        raise InconsistentSceneRegistrationError(str(exc)) from exc
    return RegisteredSceneRevision(
        decision=registered.decision, revision=registered.revision
    )


__all__ = [
    "InconsistentSceneRegistrationError",
    "RegisteredSceneRevision",
    "SceneManifestRejectedError",
    "SceneRegistration",
    "SceneRegistrationConflictError",
    "SceneRegistrationError",
    "SceneRegistrationScopeError",
    "VerifiedSceneManifest",
    "register_scenes",
]
