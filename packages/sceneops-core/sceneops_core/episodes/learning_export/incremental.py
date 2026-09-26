"""Incremental learning-data export planning (SceneOps V2 Request 5.5).

Pure planning only -- no I/O, no ArtifactStore, no DB, no Parquet. Given a
base export's manifest and the full target set of pinned revisions
(base's own inputs plus a delta), decides which of the base's physical
shards can be reused verbatim (SceneOps V2 Request 5.2's shard/row-group
layout is never repacked -- see this module's own §3 reasoning below) and
where new shards must be appended.

::

    base_manifest.inputs + delta revisions = target_revisions
            -> plan_incremental_export()
            -> IncrementalExportPlan
                    .reused_learning_steps_shards / .reused_learning_signals_shards
                       (base_manifest.shard_index's own shard objects, verbatim)
                    .new_episode_refs (the delta, sorted)
                    .start_shard_index (where new shards must begin numbering)

Chosen strategy: append-only delta shards (never rewrite an existing
shard, even an under-filled tail one) -- the smallest strategy that keeps
determinism and full artifact/lineage reuse without repacking the export,
at the accepted cost of not repacking small shards from many small
incremental deltas over time (compaction is explicitly out of this
request's scope; see the module docstring in the writer-side counterpart
for the full comparison against tail-shard rewrite).

Incremental export supports pure *addition* only: the target set must be
a superset of the base's exposed set. Dropping or replacing an EpisodeRef
(a tombstone/removal operation) is out of scope -- callers who need that
use a full export instead, which remains completely unaffected by
anything in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.episodes.learning import EpisodeRef

from .schemas import AlignedArtifactRevision, LearningDataExportManifest
from .sharding import LearningDataShard


class IncrementalExportUnsupportedBaseError(Exception):
    """The base export cannot be used as an incremental base -- either it
    has no ``shard_index`` (a v1/legacy single-file export -- nothing
    physical to reuse), or the target revision set does not fully contain
    the base's exposed set (this module supports additive delta only)."""


class IncrementalExportOverlapError(Exception):
    """``target_revisions`` contains more than one entry for the same
    ``(episode_id, aligned_artifact_checksum)`` pair -- always a caller
    error (e.g. accidentally resubmitting an already-included revision as
    part of the delta), never a valid input."""


@dataclass(frozen=True)
class IncrementalExportPlan:
    """What an incremental export needs to do, decided once, without any
    I/O (SceneOps V2 Request 5.5 §4).

    ``reused_learning_steps_shards``/``reused_learning_signals_shards``
    are the base manifest's own ``LearningDataShard`` objects, completely
    unchanged (same ``uri``/``checksum``/``size_bytes``/``episodes``) --
    executing this plan must never rewrite or re-upload them, and must
    never create a new ``ArtifactRecord`` for them (their base export's
    own record remains the authoritative lineage entry).

    ``new_episode_refs`` is the delta, in the same deterministic
    ``(episode_id, aligned_artifact_checksum)`` sorted order
    ``plan_shards_for_entries`` already uses -- executing this plan bins
    *only* these into new shards, via the existing (unmodified)
    ``plan_episode_shards``/``plan_shards_for_entries``, starting at
    ``start_shard_index`` so a new shard's index never collides with a
    reused one's.
    """

    base_export_id: str
    reused_learning_steps_shards: tuple[LearningDataShard, ...]
    reused_learning_signals_shards: tuple[LearningDataShard, ...]
    reused_episode_refs: tuple[EpisodeRef, ...]
    new_episode_refs: tuple[EpisodeRef, ...]
    start_shard_index: int


def _sorted_refs(keys: set[tuple[str, str]]) -> tuple[EpisodeRef, ...]:
    return tuple(
        sorted(
            (
                EpisodeRef(episode_id=episode_id, aligned_artifact_checksum=checksum)
                for episode_id, checksum in keys
            ),
            key=lambda ref: (ref.episode_id, ref.aligned_artifact_checksum),
        )
    )


def plan_incremental_export(
    base_manifest: LearningDataExportManifest,
    target_revisions: list[AlignedArtifactRevision],
) -> IncrementalExportPlan:
    """Diff ``target_revisions`` (the full desired final set -- base's own
    inputs plus a delta, already merged by the caller) against
    ``base_manifest`` to decide what can be reused.

    Deterministic and side-effect-free: calling this twice with the same
    arguments always returns an equal plan. Does not itself bin
    ``new_episode_refs`` into shards -- that requires each new episode's
    actual row count (from its parsed ``AlignedEpisodeArtifact``, not
    available at this identity-only planning layer) and a ``ShardPolicy``,
    both supplied later by the execution layer
    (``sceneops_analytics.incremental_export``), which reuses the
    existing, unmodified ``plan_episode_shards``/``plan_shards_for_entries``
    for that step.
    """
    if base_manifest.shard_index is None:
        raise IncrementalExportUnsupportedBaseError(
            f"base export {base_manifest.export_id!r} has no shard_index "
            "(v1 single-file layout) -- nothing physical to reuse; use a "
            "full export instead"
        )

    target_keys = [
        (item.episode_id, item.aligned_artifact_checksum) for item in target_revisions
    ]
    if len(set(target_keys)) != len(target_keys):
        raise IncrementalExportOverlapError(
            "target_revisions contains duplicate (episode_id, "
            "aligned_artifact_checksum) pairs"
        )
    target_key_set = set(target_keys)

    base_key_set = {
        (item.episode_id, item.aligned_artifact_checksum)
        for item in base_manifest.inputs
    }
    if not base_key_set.issubset(target_key_set):
        missing = sorted(base_key_set - target_key_set)
        preview = missing[:5]
        raise IncrementalExportUnsupportedBaseError(
            f"base export {base_manifest.export_id!r} declares "
            f"{len(missing)} EpisodeRef(s) not present in target_revisions "
            f"(e.g. {preview}) -- incremental export only supports pure "
            "addition; use a full export to drop/replace an EpisodeRef"
        )

    new_key_set = target_key_set - base_key_set
    if not new_key_set:
        raise ValueError(
            "plan_incremental_export: target_revisions adds nothing new "
            f"over base export {base_manifest.export_id!r}"
        )

    return IncrementalExportPlan(
        base_export_id=base_manifest.export_id,
        reused_learning_steps_shards=tuple(base_manifest.shard_index.learning_steps),
        reused_learning_signals_shards=tuple(
            base_manifest.shard_index.learning_signals
        ),
        reused_episode_refs=_sorted_refs(base_key_set),
        new_episode_refs=_sorted_refs(new_key_set),
        start_shard_index=len(base_manifest.shard_index.learning_steps),
    )


__all__ = [
    "IncrementalExportOverlapError",
    "IncrementalExportPlan",
    "IncrementalExportUnsupportedBaseError",
    "plan_incremental_export",
]
