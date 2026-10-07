"""CachePolicy: bounded-cache configuration for one SceneOpsDataset
instance (SceneOps V2 Request 5.4 §2).

Purely a performance/memory-footprint knob, never part of logical dataset
semantics -- two ``SceneOpsDataset`` instances opened over the identical
manifest with different ``CachePolicy`` values must produce identical
``get_window()``/``get_step()``/``resolve_feature_schema()`` results,
differing only in what gets re-fetched vs reused. Nothing here is passed
to, or changes the behavior of, ``sceneops_core.episodes.learning``'s
pure projection functions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CachePolicy:
    """Maximum entry counts for each of ``SceneOpsDataset``'s in-memory
    caches. ``0`` disables that cache entirely (nothing is ever kept --
    for benchmarking/debugging raw I/O cost); ``None`` means unbounded
    (the pre-Request-5.4 default behavior, available but never the
    ``SceneOpsDataset.open()`` default).

    Defaults were chosen from measured per-entry memory footprint across
    the Request 5.1/5.2 scale ladder (see
    ``docs/history/learning-data-scaling-baseline.md`` §42): a
    reconstructed episode's full step list is by far the dominant cost
    (0.6-2.7 MB/episode observed, scaling with step_count x channel
    count) -- ``max_episode_steps`` is deliberately small relative to
    realistic dataset sizes. A resolved ``FeatureSchema`` is small
    (dimension/kind metadata only, no step data), and a shard's parsed
    Parquet footer metadata is smaller still (~8 KB observed) and
    naturally bounded by shard count (which grows far slower than episode
    count under Request 5.2's shard policy) -- both get looser default
    bounds reflecting that.
    """

    max_episode_steps: int | None = 64
    max_schemas: int | None = 128
    max_shard_metadata: int | None = 256

    @property
    def episode_steps_enabled(self) -> bool:
        return self.max_episode_steps != 0


DEFAULT_CACHE_POLICY = CachePolicy()

# All caching off -- for benchmarking/debugging raw selective-read I/O
# cost without any cache effects (Request 5.4 §2's explicit requirement).
DISABLED_CACHE_POLICY = CachePolicy(
    max_episode_steps=0, max_schemas=0, max_shard_metadata=0
)


__all__ = ["DEFAULT_CACHE_POLICY", "DISABLED_CACHE_POLICY", "CachePolicy"]
