"""Physical shard layout for learning_steps/learning_signals (SceneOps V2
Request 5.2). Pure domain models + a pure, deterministic shard-assignment
function -- no I/O, no Parquet, no ArtifactStore. sceneops-analytics writes
the actual shard files (see ``sceneops_analytics.learning_tables_sharded``)
and populates ``LearningDataShard.uri``/``checksum``/``size_bytes`` with the
results; this module only decides *which EpisodeRefs go in which shard*.

Kept fully separate from EpisodeRef logical identity (frozen, SceneOps V2
Request 2.7A §3): a shard is a purely physical grouping of rows belonging to
EpisodeRefs that already exist and mean exactly what they meant before
sharding existed. ``learning_episodes`` is never sharded (it is already
metadata-scale, one row per EpisodeRef) -- this module's models describe
``learning_steps``/``learning_signals`` layout only.
"""

from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.learning import EpisodeRef

# Physical-layout version tag (distinct from LEARNING_DATA_SCHEMA_VERSION,
# which versions the *logical* column set/semantics, Request 2.5 §6).
# Informational only -- readers dispatch on whether
# LearningDataExportManifest.shard_index is populated, never on this
# string; it exists purely so a human (or a future migration script)
# reading a manifest doesn't have to infer the layout from field presence.
LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE = "v1-single-file"
LEARNING_DATA_LAYOUT_VERSION_SHARDED = "v2-sharded"


class ShardPolicy(SceneOpsBaseModel):
    """Bounds used to decide shard boundaries (SceneOps V2 Request 5.2 §3).
    Both bounds apply; a shard closes as soon as adding the next episode
    would exceed either one. ``max_rows_per_shard`` is evaluated against
    each episode's ``learning_steps`` row count (its ``step_count``) for
    *both* tables sharded this way -- a documented approximation, since
    ``learning_signals`` rows-per-episode also depends on channel count,
    which this policy does not track separately (SceneOps V2 Request 5.2
    §16)."""

    max_episodes_per_shard: int
    max_rows_per_shard: int


class ShardEpisodeMember(SceneOpsBaseModel):
    """One EpisodeRef's position within one physical shard file (SceneOps
    V2 Request 5.2 §4). ``row_group_index`` is that EpisodeRef's exact
    Parquet row group inside the shard -- shards are written with exactly
    one row group per episode (episode-aligned row groups), so
    ``row_group_index`` is always this member's position in its shard's
    ``episodes`` list. ``row_count`` is this episode's row count in
    *this table* (step_count for a learning_steps shard, the episode's
    actual signals row count for a learning_signals shard -- these differ,
    since one table is exact per-episode truth and the other was only
    planned against the step_count approximation above)."""

    episode_ref: EpisodeRef
    row_group_index: int
    row_count: int


class LearningDataShard(SceneOpsBaseModel):
    """One physical Parquet object for one logical table
    (``learning_steps`` or ``learning_signals``), holding a bounded,
    ordered subset of EpisodeRefs (SceneOps V2 Request 5.2 §3/§4).
    ``shard_index`` is this shard's position among all shards written for
    this table in this export -- also embedded in its own URI/filename for
    human debuggability, but the manifest (not the URI) is the source of
    truth a reader must use."""

    shard_index: int
    uri: str
    checksum: str
    size_bytes: int
    row_count: int
    episodes: list[ShardEpisodeMember] = Field(default_factory=list)


class LearningDataShardIndex(SceneOpsBaseModel):
    """Physical shard layout for one export's ``learning_steps``/
    ``learning_signals`` tables (SceneOps V2 Request 5.2 §4). Both tables
    share the identical episode -> shard assignment and per-shard episode
    order (``shard_policy`` decided this once, from each episode's
    step_count) -- only ``row_count``/``row_group_index`` values differ
    per table, since a signals shard's actual row counts are exact, not
    the step_count approximation planning used. A table name absent from
    the export's requested tables (``LearningDataExportConfig.tables``)
    has an empty list here, mirroring how ``table_uris`` simply omits a
    skipped table today."""

    shard_policy: ShardPolicy
    learning_steps: list[LearningDataShard] = Field(default_factory=list)
    learning_signals: list[LearningDataShard] = Field(default_factory=list)


def default_shard_policy() -> ShardPolicy:
    """Production default (SceneOps V2 Request 5.2 §3/§7): bounded so a
    single-digit-thousands-episode export still produces a small,
    human-inspectable number of shard files (a 10,000-episode export at
    this policy tops out around 50 shards per table, never one file per
    episode), while keeping each shard small enough that "read one
    episode's own shard" stays a small, cheap object relative to the
    whole export -- see docs/architecture/learning-data-scaling-baseline.md
    for the measurements this default is based on. ``max_rows_per_shard``
    is generous relative to a typical per-episode step_count specifically
    so short/typical episodes are bounded by episode count, not row count,
    in the common case -- the row bound exists to protect against a
    minority of very long episodes, not to be the usual binding
    constraint."""
    return ShardPolicy(max_episodes_per_shard=200, max_rows_per_shard=200_000)


def plan_episode_shards(
    episodes: list[tuple[EpisodeRef, int]], policy: ShardPolicy
) -> list[list[tuple[EpisodeRef, int]]]:
    """Deterministic shard assignment (SceneOps V2 Request 5.2 §3): a
    simple sequential greedy bin-fill, NOT an optimal bin-packing --
    ``episodes`` is consumed in the exact order given (callers must
    already have sorted it, e.g. by ``(episode_id,
    aligned_artifact_checksum)``, for reproducible, locality-preserving
    shard membership) and a new shard starts as soon as adding the next
    episode would exceed either ``max_episodes_per_shard`` or
    ``max_rows_per_shard`` -- never by reordering episodes to pack shards
    tighter. A single episode whose own row count already exceeds
    ``max_rows_per_shard`` is never split -- it becomes a one-episode
    shard on its own rather than raising, since Parquet row groups (and
    this design's one-row-group-per-episode invariant) require an
    episode's rows to stay contiguous and undivided.

    Every returned shard has at least one episode; ``episodes=[]`` returns
    ``[]`` (zero shards), never one empty shard.
    """
    if policy.max_episodes_per_shard <= 0:
        raise ValueError(
            f"max_episodes_per_shard must be > 0, got {policy.max_episodes_per_shard}"
        )
    if policy.max_rows_per_shard <= 0:
        raise ValueError(
            f"max_rows_per_shard must be > 0, got {policy.max_rows_per_shard}"
        )

    shards: list[list[tuple[EpisodeRef, int]]] = []
    current: list[tuple[EpisodeRef, int]] = []
    current_rows = 0
    for ref, row_count in episodes:
        would_exceed_count = len(current) + 1 > policy.max_episodes_per_shard
        would_exceed_rows = current_rows + row_count > policy.max_rows_per_shard
        if current and (would_exceed_count or would_exceed_rows):
            shards.append(current)
            current = []
            current_rows = 0
        current.append((ref, row_count))
        current_rows += row_count
    if current:
        shards.append(current)
    return shards


__all__ = [
    "LEARNING_DATA_LAYOUT_VERSION_SHARDED",
    "LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE",
    "LearningDataShard",
    "LearningDataShardIndex",
    "ShardEpisodeMember",
    "ShardPolicy",
    "default_shard_policy",
    "plan_episode_shards",
]
