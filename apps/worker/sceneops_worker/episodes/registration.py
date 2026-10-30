"""REGISTER_EPISODES: the only writer of canonical Episode units and
revisions (ADR-015 §6.4, MS-7).

Input is the ids of the EPISODE_MANIFEST ArtifactRecords of one build (one
source, one producer fingerprint). Registration verifies, then adds
immutable revisions; it never writes manifest or payload bytes, never reads
a Scene or a SourceSnapshot, and never reads or writes a DatasetVersion:
membership is a separate composition that pins the revisions registration
returns.

    E1  manifest gate, for every input artifact, against the ArtifactStore
        itself and not only against database rows: the ArtifactRecord exists,
        is an EPISODE_MANIFEST and pins a checksum and size; its bytes are
        read back and match both, and are a canonical EpisodeManifest v4
        (strict parse, fingerprint re-derived); its id is the revision's
        deterministic manifest artifact id
    E2  derive each Episode's unit id; reject duplicate units, more than one
        source or producer fingerprint, and a source binding that is not the
        registered source; then verify every payload reference against that
        source exactly as the Scene registrar does
        (``sceneops_worker.payload_registration``)
    E3  one transaction: register each unit and revision additively
        (sceneops_db.repositories.revisions), in unit id order; commit
          same unit + same manifest checksum          -> no-op
          new unit, or a new producer fingerprint     -> insert
          same unit + same fingerprint + other checksum
                                                      -> conflict

Failure, retry and concurrency semantics are the Scene registrar's
(``sceneops_worker.scenes.registration``): E1-E2 failures leave no DB
change, an E3 failure rolls back the whole input, and the unique keys decide
concurrent registrations without a lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord, PayloadRef
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeManifestError,
    EpisodeRevisionRecord,
    project_episode_revision,
)
from sceneops_core.provenance import (
    InconsistentRevisionsError,
    NonDeterministicProducerError,
    RevisionDecision,
    RevisionPin,
)
from sceneops_db.repositories import UnitIdentityConflictError

from sceneops_worker.core.context import WorkerContext
from sceneops_episodes.artifacts import EpisodeManifestIntegrityError
from sceneops_worker.payload_registration import (
    PayloadRegistrationError,
    verify_content_payload_refs,
    verify_recording_payload_refs,
)
from sceneops_worker.sources.content import (
    RegisteredContentError,
    resolve_registered_content,
)


class EpisodeRegistrationError(RuntimeError):
    """Base class. No REGISTER_EPISODES failure leaves a canonical change."""


class EpisodeManifestRejectedError(EpisodeRegistrationError):
    """An input artifact, or an object it references, failed the durability
    and integrity gate."""


class EpisodeRegistrationScopeError(EpisodeRegistrationError):
    """The input set is not the output of one build of one registered
    source (a RobotRun or a SourceContent)."""


class EpisodeRegistrationConflictError(EpisodeRegistrationError):
    """A unit already has another revision under the same producer
    fingerprint (a non-deterministic producer)."""


class InconsistentEpisodeRegistrationError(EpisodeRegistrationError):
    """Committed units or revisions violate a registrar invariant. Reported,
    never repaired."""


@dataclass(frozen=True)
class VerifiedEpisodeManifest:
    artifact: ArtifactRecord
    manifest: EpisodeManifest
    record: EpisodeRevisionRecord


@dataclass(frozen=True)
class RegisteredEpisodeRevision:
    decision: RevisionDecision
    revision: EpisodeRevisionRecord

    @property
    def pin(self) -> RevisionPin:
        return self.revision.pin()


@dataclass(frozen=True)
class EpisodeRegistration:
    source_kind: str
    source_id: str
    producer_fingerprint: str
    # One per input manifest, in unit id order.
    revisions: list[RegisteredEpisodeRevision]

    @property
    def pins(self) -> list[RevisionPin]:
        return [r.pin for r in self.revisions]

    def episode_ids(self, decision: RevisionDecision | None = None) -> list[str]:
        return [
            r.revision.episode_id
            for r in self.revisions
            if decision is None or r.decision == decision
        ]


async def register_episodes(
    *, context: WorkerContext, manifest_artifact_ids: list[str]
) -> EpisodeRegistration:
    if not manifest_artifact_ids:
        raise EpisodeRegistrationScopeError("no episode manifests to register")
    if len(set(manifest_artifact_ids)) != len(manifest_artifact_ids):
        raise EpisodeRegistrationScopeError("manifest_artifact_ids contains duplicates")

    verified = [
        await _verify_manifest(context, artifact_id=artifact_id)
        for artifact_id in manifest_artifact_ids
    ]
    _check_input_scope(verified)
    if verified[0].record.source_kind == "recording":
        await _verify_recording_source(context, verified)
    else:
        await _verify_content_source(context, verified)

    try:
        registered = [
            await _register(context, item)
            for item in sorted(verified, key=lambda v: v.record.episode_id)
        ]
        await context.commit()
    except BaseException:
        await context.rollback()
        raise
    return EpisodeRegistration(
        source_kind=verified[0].record.source_kind,
        source_id=verified[0].record.source_id,
        producer_fingerprint=verified[0].record.producer_fingerprint,
        revisions=registered,
    )


async def _verify_manifest(
    context: WorkerContext, *, artifact_id: str
) -> VerifiedEpisodeManifest:
    artifact = await context.artifact_record_store.get(artifact_id)
    if artifact is None:
        raise EpisodeManifestRejectedError(
            f"manifest artifact not found: {artifact_id}"
        )
    if artifact.kind != ArtifactKind.EPISODE_MANIFEST:
        raise EpisodeManifestRejectedError(
            f"artifact {artifact_id} is a {artifact.kind!r}, not a canonical "
            f"{ArtifactKind.EPISODE_MANIFEST.value!r}"
        )
    if not artifact.checksum or artifact.size_bytes is None:
        raise EpisodeManifestRejectedError(
            f"artifact {artifact_id} does not pin a checksum and size"
        )
    try:
        manifest = await context.episode_artifact_store.read_pinned_manifest(
            uri=artifact.uri, checksum=artifact.checksum, size_bytes=artifact.size_bytes
        )
    except (EpisodeManifestIntegrityError, EpisodeManifestError) as exc:
        raise EpisodeManifestRejectedError(f"artifact {artifact_id}: {exc}") from exc
    try:
        record = project_episode_revision(
            manifest=manifest,
            manifest_artifact_id=artifact.artifact_id,
            manifest_checksum=artifact.checksum,
        )
    except ValueError as exc:
        # e.g. an artifact id that is not the revision's deterministic id.
        raise EpisodeManifestRejectedError(f"artifact {artifact_id}: {exc}") from exc
    return VerifiedEpisodeManifest(artifact=artifact, manifest=manifest, record=record)


def _check_input_scope(verified: list[VerifiedEpisodeManifest]) -> None:
    episode_ids = [v.record.episode_id for v in verified]
    duplicates = sorted({e for e in episode_ids if episode_ids.count(e) > 1})
    if duplicates:
        raise EpisodeRegistrationScopeError(
            f"input contains more than one manifest for episode(s) {duplicates}"
        )
    sources = {(v.record.source_kind, v.record.source_id) for v in verified}
    if len(sources) != 1:
        raise EpisodeRegistrationScopeError(
            f"a registration covers exactly one source, got {sorted(sources)}"
        )
    fingerprints = {v.record.producer_fingerprint for v in verified}
    if len(fingerprints) != 1:
        raise EpisodeRegistrationScopeError(
            "a registration covers one build with one producer fingerprint, got "
            f"{len(fingerprints)}"
        )


async def _verify_recording_source(
    context: WorkerContext, verified: list[VerifiedEpisodeManifest]
) -> None:
    """The manifests must have been built from the RobotRun's registered
    recording bytes, and every payload they reference must be one of its
    registered observation payloads, as recorded and as stored. Their
    window clock is the producer's declared segmentation clock, not
    necessarily the recording clock, so it is not compared with the
    RobotRun."""
    robot_run_id = verified[0].record.source_id
    run = await context.robot_store.get_run(robot_run_id)
    if run is None:
        raise EpisodeRegistrationScopeError(
            f"RobotRun is not registered: {robot_run_id}"
        )
    recording = await context.artifact_record_store.get(run.recording_artifact_id)
    if recording is None:
        raise InconsistentEpisodeRegistrationError(
            f"RobotRun {robot_run_id} references missing recording artifact "
            f"{run.recording_artifact_id}"
        )
    for item in verified:
        source = item.manifest.lineage.source
        if source.recording_checksum != recording.checksum:
            raise EpisodeRegistrationScopeError(
                f"manifest was built from recording bytes {source.recording_checksum}, "
                f"but RobotRun {robot_run_id} is {recording.checksum}"
            )
    try:
        await verify_recording_payload_refs(context, run, _payload_refs(verified))
    except PayloadRegistrationError as exc:
        raise EpisodeManifestRejectedError(str(exc)) from exc


async def _verify_content_source(
    context: WorkerContext, verified: list[VerifiedEpisodeManifest]
) -> None:
    """The manifests must have been built from the registered SourceContent's
    bytes, and every preserved file they reference must be that content's,
    as its inventory pins it and as the store holds it."""
    content_id = verified[0].record.source_id
    try:
        content = await resolve_registered_content(context, content_id)
    except RegisteredContentError as exc:
        raise EpisodeRegistrationScopeError(str(exc)) from exc
    for item in verified:
        source = item.manifest.lineage.source
        if source.content_checksum != content.record.content_checksum:
            raise EpisodeRegistrationScopeError(
                f"manifest was built from content bytes {source.content_checksum}, "
                f"but SourceContent {content_id} is {content.record.content_checksum}"
            )
    try:
        await verify_content_payload_refs(context, content, _payload_refs(verified))
    except PayloadRegistrationError as exc:
        raise EpisodeManifestRejectedError(str(exc)) from exc


def _payload_refs(verified: list[VerifiedEpisodeManifest]) -> list[PayloadRef]:
    """Every payload reference of the input, for one batched source check."""
    return [
        o.payload
        for item in verified
        for o in item.manifest.occurrences()
        if o.payload is not None
    ]


async def _register(
    context: WorkerContext, item: VerifiedEpisodeManifest
) -> RegisteredEpisodeRevision:
    try:
        registered = await context.episode_store.register_revision(item.record)
    except NonDeterministicProducerError as exc:
        raise EpisodeRegistrationConflictError(str(exc)) from exc
    except (InconsistentRevisionsError, UnitIdentityConflictError) as exc:
        raise InconsistentEpisodeRegistrationError(str(exc)) from exc
    return RegisteredEpisodeRevision(
        decision=registered.decision, revision=registered.revision
    )


__all__ = [
    "EpisodeManifestRejectedError",
    "EpisodeRegistration",
    "EpisodeRegistrationConflictError",
    "EpisodeRegistrationError",
    "EpisodeRegistrationScopeError",
    "InconsistentEpisodeRegistrationError",
    "RegisteredEpisodeRevision",
    "VerifiedEpisodeManifest",
    "register_episodes",
]
