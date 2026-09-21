"""SequenceSampler: a deterministic, framework-neutral fixed-horizon
sequence index over SceneOpsDataset (SceneOps V2 Request 2.7C).

::

    SceneOpsDataset.episodes() + EpisodeMetadata.step_count
            -> per-Episode valid-window counts (no step/signal I/O)
            -> cumulative index
            -> on access: SequenceRef -> SceneOpsDataset.get_window()

This module never reimplements Episode-boundary logic or projection --
every actual sample comes from the unmodified 2.7A/2.7B
get_window()/SequenceRef/SequenceSample contracts.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator

from sceneops_core.episodes.learning import (
    EpisodeRef,
    FeatureProjection,
    FeatureSchema,
    MissingFeaturePolicy,
    SequenceRef,
    SequenceSample,
)

from .dataset import SceneOpsDataset
from .errors import SamplerSchemaMismatchError


def _window_count(step_count: int, horizon: int, stride: int) -> int:
    """Number of valid start_steps for one Episode (SceneOps V2 Request
    2.7C §3): 0, S, 2S, ... while start + horizon <= step_count. An Episode
    shorter than horizon contributes zero windows."""
    if step_count < horizon:
        return 0
    return (step_count - horizon) // stride + 1


class SequenceSampler:
    """Deterministic index over every valid fixed-horizon window across
    every EpisodeRef a SceneOpsDataset exposes (SceneOps V2 Request 2.7C).

    Construct via ``await SequenceSampler.create(...)`` -- never directly.

    Global ordering follows ``dataset.episodes()`` (itself deterministic
    and curation-aware, per Request 2.7B) and, within each EpisodeRef,
    increasing start_step. Never shuffled here -- shuffling is downstream
    framework/training code's responsibility, not this canonical index's.

    One sampler instance uses one FeatureProjection for its lifetime. Every
    EpisodeRef this sampler could ever draw a window from must resolve that
    projection to one identical FeatureSchema (checked once, at
    construction) -- otherwise ``get(i)`` could return incompatible shapes
    across ``i``. Episodes shorter than ``horizon`` never contribute a
    window and are therefore never schema-checked at all: their schema
    (compatible or not) is irrelevant to a sampler that will never draw
    from them.

    Index representation: a small list of (EpisodeRef, window_count) pairs
    plus parallel cumulative offsets -- one entry per *contributing*
    Episode, not one entry per window. ``len(sampler)`` can be in the
    millions without this sampler ever materializing a
    list[SequenceRef] that large; ``sequence_ref(i)``/``get(i)`` compute
    the i-th SequenceRef on demand via a binary search over the cumulative
    offsets.
    """

    def __init__(
        self,
        *,
        dataset: SceneOpsDataset,
        projection: FeatureProjection,
        horizon: int,
        stride: int,
        episode_windows: list[tuple[EpisodeRef, int]],
        cumulative_offsets: list[int],
        total_windows: int,
        feature_schema: FeatureSchema | None,
    ) -> None:
        self._dataset = dataset
        self._projection = projection
        self._horizon = horizon
        self._stride = stride
        self._episode_windows = episode_windows
        self._cumulative_offsets = cumulative_offsets
        self._total_windows = total_windows
        self._feature_schema = feature_schema

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        dataset: SceneOpsDataset,
        *,
        projection: FeatureProjection,
        horizon: int,
        stride: int = 1,
    ) -> "SequenceSampler":
        """Build the sequence index over every EpisodeRef ``dataset``
        currently exposes (SceneOps V2 Request 2.7C §9): uses only
        ``dataset.episodes()``/``EpisodeMetadata.step_count`` (already
        loaded, no I/O) to compute per-Episode window counts -- never calls
        ``get_window()`` here.

        Then, for every Episode that contributes at least one window,
        resolves its FeatureSchema for ``projection`` exactly once (Request
        2.7C §7) -- proportional to the number of contributing Episodes,
        never to the number of windows -- and requires every one of those
        schemas to be identical, raising SamplerSchemaMismatchError at the
        first disagreement rather than deferring the failure to some later
        ``get(i)`` call.
        """
        if horizon <= 0:
            raise ValueError(f"horizon must be > 0, got {horizon}")
        if stride <= 0:
            raise ValueError(f"stride must be > 0, got {stride}")

        episode_windows: list[tuple[EpisodeRef, int]] = []
        for ref in dataset.episodes():
            step_count = dataset.get_episode(ref).step_count
            count = _window_count(step_count, horizon, stride)
            if count > 0:
                episode_windows.append((ref, count))

        feature_schema: FeatureSchema | None = None
        for ref, _count in episode_windows:
            schema = await dataset.resolve_feature_schema(ref, projection)
            if feature_schema is None:
                feature_schema = schema
            elif schema != feature_schema:
                raise SamplerSchemaMismatchError(
                    f"{ref!r} resolves FeatureProjection to a FeatureSchema "
                    f"incompatible with earlier Episodes in this sampler "
                    f"(observation_dim={schema.observation_dim} vs "
                    f"{feature_schema.observation_dim}, "
                    f"action_dim={schema.action_dim} vs "
                    f"{feature_schema.action_dim})"
                )

        cumulative_offsets: list[int] = []
        offset = 0
        for _ref, count in episode_windows:
            cumulative_offsets.append(offset)
            offset += count

        return cls(
            dataset=dataset,
            projection=projection,
            horizon=horizon,
            stride=stride,
            episode_windows=episode_windows,
            cumulative_offsets=cumulative_offsets,
            total_windows=offset,
            feature_schema=feature_schema,
        )

    # ------------------------------------------------------------------
    # inspection
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return self._total_windows

    @property
    def horizon(self) -> int:
        return self._horizon

    @property
    def stride(self) -> int:
        return self._stride

    @property
    def projection(self) -> FeatureProjection:
        return self._projection

    @property
    def feature_schema(self) -> FeatureSchema | None:
        """None only when this sampler has zero contributing Episodes (so
        len(sampler) == 0) -- there is then no schema to report."""
        return self._feature_schema

    # ------------------------------------------------------------------
    # index access
    # ------------------------------------------------------------------

    def sequence_ref(self, index: int) -> SequenceRef:
        """The index-th SequenceRef in canonical order (SceneOps V2
        Request 2.7C §4/§5) -- computed on demand via a binary search over
        the per-Episode cumulative offsets, never by materializing every
        preceding SequenceRef."""
        if not (0 <= index < self._total_windows):
            raise IndexError(
                f"index {index} out of range for sampler of length "
                f"{self._total_windows}"
            )
        episode_index = bisect_right(self._cumulative_offsets, index) - 1
        ref, _count = self._episode_windows[episode_index]
        local_index = index - self._cumulative_offsets[episode_index]
        start_step = local_index * self._stride
        return SequenceRef(
            episode_ref=ref, start_step=start_step, horizon=self._horizon
        )

    def sequence_refs(self) -> Iterator[SequenceRef]:
        """Every SequenceRef this sampler indexes, in canonical order
        (dataset.episodes() order, then increasing start_step) -- a lazy
        generator, never a materialized list (SceneOps V2 Request 2.7C
        §10)."""
        for ref, count in self._episode_windows:
            for i in range(count):
                yield SequenceRef(
                    episode_ref=ref, start_step=i * self._stride, horizon=self._horizon
                )

    async def get(self, index: int) -> SequenceSample:
        """Project the index-th window (SceneOps V2 Request 2.7C §2/§8):
        delegates entirely to SceneOpsDataset.get_window() -- no second
        projection/window path. A geometrically valid window is not
        guaranteed to be projectable: an ABSENT/MISSING required feature
        propagates FeatureAbsentError/FeatureMissingError unchanged rather
        than being silently skipped."""
        ref = self.sequence_ref(index)
        return await self._dataset.get_window(
            ref.episode_ref,
            ref.start_step,
            ref.horizon,
            self._projection,
            missing_policy=MissingFeaturePolicy.ERROR,
        )


__all__ = ["SequenceSampler"]
