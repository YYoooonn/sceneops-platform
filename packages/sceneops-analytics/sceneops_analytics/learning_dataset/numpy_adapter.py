"""Framework-neutral NumPy consumer conversion (SceneOps V2 Request 2.7D).

::

    SequenceSample (plain Python lists, pydantic)
            -> to_numpy()
            -> NumPySequenceSample (ndarrays)

    SequenceSampler + explicit indices
            -> materialize_sequences() (async, explicit)
            -> list[NumPySequenceSample]

This module has no PyTorch dependency -- it is the boundary any training
framework (NumPy-native code, JAX, TensorFlow, or the optional
sceneops_analytics.learning_dataset.torch_adapter) can build on. It performs
no dataset semantics of its own: no windowing, no schema resolution, no
missing-value policy -- those stay owned by SceneOpsDataset/SequenceSampler
(sceneops_core.episodes.learning, Request 2.7A-C), which this module calls
unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from sceneops_core.episodes.learning import EpisodeRef, SequenceSample

from .sampler import SequenceSampler

# SceneOps V2 Request 2.7D §4: one stable default dense-array dtype for
# training consumption. float32 (not float64) because it is what virtually
# every training framework/optimizer defaults to, and it halves the memory/
# bandwidth cost of the columnar sources are float64-precision anyway.
# Callers who genuinely need float64 can pass dtype=np.float64 explicitly.
DEFAULT_DTYPE = np.float32


@dataclass(frozen=True)
class NumPySequenceSample:
    """NumPy consumer representation of one SequenceSample (SceneOps V2
    Request 2.7D §4/§5).

    Carries just enough identity to trace a sample back to SceneOps --
    EpisodeRef + start_step + horizon -- never a random/synthetic id and
    never the full upstream alignment/provenance metadata (that stays on
    the canonical AlignedEpisode/LearningStep this sample was projected
    from, reachable via SceneOpsDataset if genuinely needed).

    A plain frozen dataclass, not a SceneOpsBaseModel -- ndarray fields
    have no meaningful JSON/pydantic serialization here, and this type
    carries no domain validation of its own (that already happened in
    sceneops_core.episodes.learning's pure projection).
    """

    episode_ref: EpisodeRef
    start_step: int
    horizon: int

    timestamps_us: np.ndarray  # [T], int64
    observation: np.ndarray  # [T, observation_dim], DEFAULT_DTYPE unless overridden
    action: np.ndarray  # [T, action_dim], DEFAULT_DTYPE unless overridden


def to_numpy(
    sample: SequenceSample, *, dtype: np.dtype = DEFAULT_DTYPE
) -> NumPySequenceSample:
    """Pure conversion, SequenceSample -> NumPySequenceSample (SceneOps V2
    Request 2.7D §4). Feature ordering is preserved exactly -- this
    function never reorders columns; that ordering was already fixed by
    the FeatureProjection that produced ``sample``.

    Never mutates ``sample``: np.array(...) (not np.asarray) always
    allocates new backing memory from the plain Python lists
    SequenceSample carries, so the two share no state.
    """
    return NumPySequenceSample(
        episode_ref=sample.episode_ref,
        start_step=sample.start_step,
        horizon=sample.horizon,
        timestamps_us=np.array(sample.timestamps_us, dtype=np.int64),
        observation=np.array(sample.observation, dtype=dtype),
        action=np.array(sample.action, dtype=dtype),
    )


async def materialize_sequences(
    sampler: SequenceSampler,
    *,
    indices: Sequence[int] | None = None,
    dtype: np.dtype = DEFAULT_DTYPE,
) -> list[NumPySequenceSample]:
    """Explicitly materialize sampler windows into NumPy samples (SceneOps
    V2 Request 2.7D §7) -- never called implicitly by any other function in
    this module or by SceneOpsTorchDataset's constructor.

    ``indices=None`` materializes every window, in the sampler's own
    canonical order (dataset.episodes() order, then increasing start_step
    -- see SequenceSampler). An explicit ``indices`` sequence is
    materialized in exactly the order given -- this function does not
    silently re-sort a caller-supplied subset back into canonical order.

    Each ``await sampler.get(i)`` call goes through the existing
    SceneOpsDataset.get_window() path unchanged -- this function adds no
    caching, filtering, or missing-value handling of its own. A window
    that fails to project (ABSENT/MISSING) raises the same
    FeatureAbsentError/FeatureMissingError sampler.get() would raise
    directly.
    """
    index_list = range(len(sampler)) if indices is None else indices
    return [to_numpy(await sampler.get(i), dtype=dtype) for i in index_list]


__all__ = ["DEFAULT_DTYPE", "NumPySequenceSample", "materialize_sequences", "to_numpy"]
