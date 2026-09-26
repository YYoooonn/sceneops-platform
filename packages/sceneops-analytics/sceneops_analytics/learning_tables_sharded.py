"""Sharded learning_steps/learning_signals writer orchestration (SceneOps
V2 Request 5.2).

::

    (checksum, AlignedEpisodeArtifact) entries
            -> plan_episode_shards() (sceneops_core, pure)
            -> per shard: build_learning_{steps,signals}_table() (Request 2.5,
               pure, reused unchanged -- called once per shard, over just
               that shard's entries)
            -> AnalyticsTableWriter.write_learning_table_shard() (Request 5.2,
               one Parquet object, one row group per episode)
            -> LearningDataShardIndex (sceneops_core, physical layout only)

Neither the logical learning_steps/learning_signals column schemas nor
ABSENT/MISSING semantics change here -- ``build_learning_steps_table``/
``build_learning_signals_table`` (learning_tables.py) are reused completely
unchanged, just invoked once per shard instead of once for the whole
export. ``learning_episodes`` is never sharded (see writer.py's existing
``write_learning_table``/``learning_table_uri`` for that single-file path,
unchanged by this module).
"""

from __future__ import annotations

import polars as pl

from sceneops_core.episodes.alignment import AlignedEpisodeArtifact
from sceneops_core.episodes.learning import EpisodeRef
from sceneops_core.episodes.learning_export import (
    IncrementalExportPlan,
    LearningDataShard,
    LearningDataShardIndex,
    ShardEpisodeMember,
    ShardPolicy,
    plan_episode_shards,
)

from .learning_tables import build_learning_signals_table, build_learning_steps_table
from .writer import AnalyticsTableWriter

_SHARDABLE_TABLE_BUILDERS = {
    "learning_steps": build_learning_steps_table,
    "learning_signals": build_learning_signals_table,
}


def _episode_ref(checksum: str, artifact: AlignedEpisodeArtifact) -> EpisodeRef:
    return EpisodeRef(
        episode_id=artifact.aligned_episode.episode_id,
        aligned_artifact_checksum=checksum,
    )


def plan_shards_for_entries(
    entries: list[tuple[str, AlignedEpisodeArtifact]], policy: ShardPolicy
) -> list[list[tuple[str, AlignedEpisodeArtifact]]]:
    """Sort ``entries`` by ``(episode_id, aligned_artifact_checksum)`` --
    the same deterministic order ``SceneOpsDataset.episodes()`` already
    returns regardless of physical layout (Request 2.7B §2), so this
    reordering is never consumer-visible -- then delegate to
    ``plan_episode_shards`` using each entry's ``step_count`` as the
    planning-size proxy for both tables (SceneOps V2 Request 5.2 §3, see
    ``ShardPolicy``'s docstring for why this is an approximation for
    learning_signals). Returns shards as lists of the original
    ``(checksum, artifact)`` entries, in per-shard episode order.
    """
    sorted_entries = sorted(
        entries, key=lambda item: (item[1].aligned_episode.episode_id, item[0])
    )
    keyed = [
        (_episode_ref(checksum, artifact), len(artifact.aligned_episode.steps))
        for checksum, artifact in sorted_entries
    ]
    entries_by_ref = {
        _episode_ref(checksum, artifact): (checksum, artifact)
        for checksum, artifact in sorted_entries
    }
    shard_plan = plan_episode_shards(keyed, policy)
    return [[entries_by_ref[ref] for ref, _row_count in shard] for shard in shard_plan]


def _per_episode_row_counts(df: pl.DataFrame) -> list[int]:
    """Each episode's exact row count in ``df``, in the order episodes
    first appear -- ``build_learning_{steps,signals}_table`` always emit
    one contiguous row block per input entry (learning_tables.py's builders
    iterate ``entries`` then, per entry, that entry's steps in order), so
    ``maintain_order=True`` recovers per-episode counts without assuming
    anything about their absolute values (needed because
    learning_signals' per-episode row count depends on channel-presence,
    not just step_count)."""
    counts = df.group_by("aligned_artifact_checksum", maintain_order=True).len()
    return counts["len"].to_list()


async def _write_sharded_table(
    writer: AnalyticsTableWriter,
    table_name: str,
    shards: list[list[tuple[str, AlignedEpisodeArtifact]]],
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    start_shard_index: int = 0,
) -> list[LearningDataShard]:
    builder = _SHARDABLE_TABLE_BUILDERS[table_name]
    results: list[LearningDataShard] = []
    for offset, shard_entries in enumerate(shards):
        shard_index = start_shard_index + offset
        df = builder(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            entries=shard_entries,
        )
        row_group_sizes = _per_episode_row_counts(df)
        write_result = await writer.write_learning_table_shard(
            table_name,
            df,
            row_group_sizes,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            shard_index=shard_index,
        )
        episode_refs = [
            _episode_ref(checksum, artifact) for checksum, artifact in shard_entries
        ]
        members = [
            ShardEpisodeMember(episode_ref=ref, row_group_index=i, row_count=row_count)
            for i, (ref, row_count) in enumerate(zip(episode_refs, row_group_sizes))
        ]
        results.append(
            LearningDataShard(
                shard_index=shard_index,
                uri=write_result.uri,
                checksum=write_result.checksum,
                size_bytes=write_result.size_bytes,
                row_count=df.height,
                episodes=members,
            )
        )
    return results


async def write_sharded_learning_tables(
    writer: AnalyticsTableWriter,
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    entries: list[tuple[str, AlignedEpisodeArtifact]],
    policy: ShardPolicy,
    table_names: set[str] = frozenset({"learning_steps", "learning_signals"}),
    start_shard_index: int = 0,
) -> LearningDataShardIndex:
    """Shard, build, and write ``learning_steps``/``learning_signals``
    (SceneOps V2 Request 5.2) -- the single orchestration entry point
    shared by the production ``EXPORT_LEARNING_DATA`` job handler and the
    scaling benchmark fixture, so shard assignment/row-group behavior is
    defined exactly once.

    ``table_names`` mirrors ``LearningDataExportConfig.tables``: a table
    name absent here is never built or written, and comes back as an
    empty list on the returned index (matching how a restricted export
    already omits a skipped table's key from ``table_uris`` today).

    ``start_shard_index`` (SceneOps V2 Request 5.5 §3) numbers the shards
    written here starting from that value instead of 0 -- used by
    incremental export to bin only the *delta* entries here while keeping
    each new shard's index (and therefore its URI, which embeds
    ``shard_index``) from ever colliding with one of the base export's
    reused shards. ``entries`` is still only ever the entries actually
    written by this call -- callers wanting reused shards to appear in the
    final index prepend them to the returned ``LearningDataShardIndex``
    themselves (see ``write_incremental_sharded_learning_tables``), since
    this function never accesses or assumes anything about a base export.
    """
    shards = plan_shards_for_entries(entries, policy)
    learning_steps = (
        await _write_sharded_table(
            writer,
            "learning_steps",
            shards,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            start_shard_index=start_shard_index,
        )
        if "learning_steps" in table_names
        else []
    )
    learning_signals = (
        await _write_sharded_table(
            writer,
            "learning_signals",
            shards,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            start_shard_index=start_shard_index,
        )
        if "learning_signals" in table_names
        else []
    )
    return LearningDataShardIndex(
        shard_policy=policy,
        learning_steps=learning_steps,
        learning_signals=learning_signals,
    )


async def write_incremental_sharded_learning_tables(
    writer: AnalyticsTableWriter,
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    delta_entries: list[tuple[str, AlignedEpisodeArtifact]],
    policy: ShardPolicy,
    plan: IncrementalExportPlan,
    table_names: set[str] = frozenset({"learning_steps", "learning_signals"}),
) -> LearningDataShardIndex:
    """The incremental counterpart to ``write_sharded_learning_tables``
    (SceneOps V2 Request 5.5 §3) -- builds and writes *only*
    ``delta_entries`` (``plan.new_episode_refs``' own
    ``(checksum, AlignedEpisodeArtifact)`` pairs, resolved by the caller),
    numbered from ``plan.start_shard_index``, then prepends ``plan``'s
    reused shards verbatim (same ``uri``/``checksum``/``size_bytes`` --
    zero bytes rewritten, zero bytes re-uploaded) to produce the complete
    shard index for the new export.

    Chose append-only delta shards over tail-shard rewrite: the base's
    existing shards (including any under-filled tail shard) are always
    left exactly as they are, so every base shard's own ``ArtifactRecord``
    stays the sole, correct lineage entry for it (a rewrite would force a
    replacement record and a duplicate upload just to top off a partially
    full shard). The accepted cost is that many small incremental deltas
    over time leave many small trailing shards uncompacted -- resolved,
    if ever needed, by a separate compaction operation outside this
    request's scope, not by this function.
    """
    new_shards = await write_sharded_learning_tables(
        writer,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
        entries=delta_entries,
        policy=policy,
        table_names=table_names,
        start_shard_index=plan.start_shard_index,
    )
    return LearningDataShardIndex(
        shard_policy=policy,
        learning_steps=[
            *plan.reused_learning_steps_shards,
            *new_shards.learning_steps,
        ],
        learning_signals=[
            *plan.reused_learning_signals_shards,
            *new_shards.learning_signals,
        ],
    )


__all__ = [
    "plan_shards_for_entries",
    "write_incremental_sharded_learning_tables",
    "write_sharded_learning_tables",
]
