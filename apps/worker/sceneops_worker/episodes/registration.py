"""REGISTER_EPISODES: the only writer of canonical Episode units and
revisions, and, until DatasetVersions are composed, of a DatasetVersion's
Episode members and Episode summary (ADR-015 §6.4; ADR-007 §17, §18, §31).

Input is a DatasetVersion and the ids of EPISODE_MANIFEST ArtifactRecords
that a producer published. Registration verifies and projects; it never
writes manifest or payload bytes, and never reads a Scene.

    E1  for every input artifact: the ArtifactRecord exists and is an
        EPISODE_MANIFEST; its bytes match the recorded size and checksum and
        are a canonical EpisodeManifest (strict parse, fingerprint re-derived);
        every payload reference resolves to an OBSERVATION_PAYLOAD
        ArtifactRecord with exactly the referenced checksum, size and media type
    E2  derive each Episode's unit id; reject duplicate units,
        more than one RobotRun or producer fingerprint, an unregistered
        RobotRun, or a manifest built from other recording bytes than the
        RobotRun's
    E3  one transaction: lock the DatasetVersion row, then
          revisions  register each unit and revision, additively
                     (sceneops_db.repositories.revisions)
          members    re-read the DatasetVersion's members of this RobotRun
                     (the recording scope) and apply §18.3 to the pins
            empty                         -> pin the complete set
            same fingerprint              -> unchanged (whole scope)
            different, replace=False      -> conflict
            different, replace=True       -> unpin absent units, repin reused
                                             units, pin new units
    E4  recompute the DatasetVersion episode_count from its members; commit

E1-E2 failures leave no DB change. Any E3 failure rolls the whole
transaction back: a registration applies its complete input or nothing,
and units are never silently skipped. Expensive reads happen before the
lock; every decision is made after it, against committed state. Revision
registration needs no lock: its unique keys decide concurrent
registrations of one unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.datasets.schemas import DatasetVersionMember
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeManifestError,
    EpisodeRevisionRecord,
    project_episode_revision,
)
from sceneops_core.provenance import (
    InconsistentRevisionsError,
    NonDeterministicProducerError,
)
from sceneops_db.repositories import UnitIdentityConflictError

from sceneops_worker.core.context import WorkerContext
from sceneops_episodes.artifacts import EpisodeManifestIntegrityError
from sceneops_worker.recordings.payload_refs import (
    PayloadIntegrityError,
    resolve_payload_artifacts,
)


class EpisodeRegistrationError(RuntimeError):
    """Base class. No REGISTER_EPISODES failure leaves a canonical change."""


class EpisodeManifestRejectedError(EpisodeRegistrationError):
    """An input artifact is not a verifiable canonical EpisodeManifest."""


class EpisodeRegistrationScopeError(EpisodeRegistrationError):
    """The input set is not one well-formed registration scope."""


class EpisodeRegistrationConflictError(EpisodeRegistrationError):
    """The DatasetVersion pins another revision of the scope and ``replace``
    is False, or a unit already has another revision under the same
    producer fingerprint (a non-deterministic producer)."""


class InconsistentEpisodeMembershipError(EpisodeRegistrationError):
    """Committed units, revisions or members violate a registrar invariant.
    Reported, never repaired."""


# Upper bound of the members one recording scope may hold.
_MAX_SCOPE_MEMBERS = 10_000


@dataclass(frozen=True)
class VerifiedEpisodeManifest:
    artifact: ArtifactRecord
    manifest: EpisodeManifest
    record: EpisodeRevisionRecord


@dataclass(frozen=True)
class EpisodeRegistration:
    dataset_id: str
    dataset_version: str
    # The revisions the DatasetVersion pins for the scope after commit.
    episodes: list[EpisodeRevisionRecord]
    created_episode_ids: list[str] = field(default_factory=list)
    replaced_episode_ids: list[str] = field(default_factory=list)
    unchanged_episode_ids: list[str] = field(default_factory=list)
    removed_episode_ids: list[str] = field(default_factory=list)


async def register_episodes(
    *,
    context: WorkerContext,
    dataset_id: str,
    dataset_version: str,
    manifest_artifact_ids: list[str],
    replace: bool = False,
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
    await _verify_recording_source(context, verified)

    try:
        try:
            await context.dataset_store.lock_version_for_update(
                dataset_id=dataset_id, version=dataset_version
            )
        except ValueError as exc:
            raise EpisodeRegistrationScopeError(str(exc)) from exc

        registration = await _apply_recording(
            context, verified, dataset_id, dataset_version, replace
        )
        episode_count = await context.episode_store.count_members(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        await context.dataset_store.update_episode_summary(
            dataset_id=dataset_id, version=dataset_version, episode_count=episode_count
        )
        await context.commit()
    except BaseException:
        await context.rollback()
        raise
    return registration


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
        await resolve_payload_artifacts(
            context.artifact_record_store,
            (o.payload for o in manifest.occurrences() if o.payload is not None),
        )
    except PayloadIntegrityError as exc:
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
    run_ids = {v.record.robot_run_id for v in verified}
    if len(run_ids) != 1:
        raise EpisodeRegistrationScopeError(
            f"a registration covers exactly one RobotRun, got {sorted(run_ids)}"
        )
    fingerprints = {v.record.producer_fingerprint for v in verified}
    if len(fingerprints) != 1:
        raise EpisodeRegistrationScopeError(
            "a recording scope has exactly one producer fingerprint, got "
            f"{len(fingerprints)}"
        )


async def _verify_recording_source(
    context: WorkerContext, verified: list[VerifiedEpisodeManifest]
) -> None:
    """The manifests must have been built from the RobotRun's registered
    recording bytes. Their window clock is the producer's declared
    segmentation clock, not necessarily the recording clock, so it is not
    compared with the RobotRun."""
    robot_run_id = verified[0].record.robot_run_id
    run = await context.robot_store.get_run(robot_run_id)
    if run is None:
        raise EpisodeRegistrationScopeError(
            f"RobotRun is not registered: {robot_run_id}"
        )
    recording = await context.artifact_record_store.get(run.recording_artifact_id)
    if recording is None:
        raise InconsistentEpisodeMembershipError(
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


async def _apply_recording(
    context: WorkerContext,
    verified: list[VerifiedEpisodeManifest],
    dataset_id: str,
    dataset_version: str,
    replace: bool,
) -> EpisodeRegistration:
    new_records: list[EpisodeRevisionRecord] = []
    for item in verified:
        try:
            registered = await context.episode_store.register_revision(item.record)
        except NonDeterministicProducerError as exc:
            raise EpisodeRegistrationConflictError(str(exc)) from exc
        except (InconsistentRevisionsError, UnitIdentityConflictError) as exc:
            raise InconsistentEpisodeMembershipError(str(exc)) from exc
        new_records.append(registered.revision)

    robot_run_id = new_records[0].robot_run_id
    fingerprint = new_records[0].producer_fingerprint
    current = await context.episode_store.list_member_revisions(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        robot_run_id=robot_run_id,
        limit=_MAX_SCOPE_MEMBERS + 1,
    )
    if len(current) > _MAX_SCOPE_MEMBERS:
        raise EpisodeRegistrationScopeError(
            f"recording scope ({dataset_id}/{dataset_version}, {robot_run_id}) has "
            f"more than {_MAX_SCOPE_MEMBERS} members"
        )
    current_fingerprints = {e.producer_fingerprint for e in current}
    if len(current_fingerprints) > 1:
        raise InconsistentEpisodeMembershipError(
            f"recording scope ({dataset_id}/{dataset_version}, {robot_run_id}) holds "
            f"{len(current_fingerprints)} producer fingerprints"
        )

    registration = EpisodeRegistration(
        dataset_id=dataset_id, dataset_version=dataset_version, episodes=[]
    )
    if current and current_fingerprints == {fingerprint}:
        registration.episodes.extend(current)
        registration.unchanged_episode_ids.extend(e.episode_id for e in current)
        return registration
    if current and not replace:
        raise EpisodeRegistrationConflictError(
            f"recording scope ({dataset_id}/{dataset_version}, {robot_run_id}) is "
            f"registered with producer fingerprint {current_fingerprints.pop()}; the "
            f"new build has {fingerprint} and replace=False"
        )

    current_ids = {e.episode_id for e in current}
    new_ids = {r.episode_id for r in new_records}
    removed = sorted(current_ids - new_ids)
    replaced = sorted(current_ids & new_ids)
    # A member is never updated: repinning a unit is unpin + pin.
    await context.dataset_store.remove_members(
        dataset_id=dataset_id, version=dataset_version, unit_ids=removed + replaced
    )
    await context.dataset_store.add_members(
        dataset_id=dataset_id,
        version=dataset_version,
        members=[
            DatasetVersionMember(
                domain="episode",
                unit_id=r.episode_id,
                manifest_artifact_id=r.manifest_artifact_id,
                manifest_checksum=r.manifest_checksum,
            )
            for r in new_records
        ],
    )
    registration.episodes.extend(new_records)
    registration.removed_episode_ids.extend(removed)
    registration.replaced_episode_ids.extend(replaced)
    registration.created_episode_ids.extend(sorted(new_ids - current_ids))
    return registration


__all__ = [
    "EpisodeManifestRejectedError",
    "EpisodeRegistration",
    "EpisodeRegistrationConflictError",
    "EpisodeRegistrationError",
    "EpisodeRegistrationScopeError",
    "InconsistentEpisodeMembershipError",
    "VerifiedEpisodeManifest",
    "register_episodes",
]
