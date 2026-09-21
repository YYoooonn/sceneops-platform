"""Optional, extremely thin PyTorch adapter (SceneOps V2 Request 2.7D §6).

Requires the ``torch`` extra (``pip install sceneops-analytics[torch]``).
This is the *only* module in sceneops_analytics that imports torch --
nothing else in this package needs it, and this module is never imported
by sceneops_analytics/__init__.py or learning_dataset/__init__.py, so
`import sceneops_analytics` never requires torch.

::

    list[NumPySequenceSample]  (already materialized -- see numpy_adapter.py)
            -> SceneOpsTorchDataset
            -> torch.Tensor conversion only, in __getitem__

Why this shape and not a lazy torch.utils.data.Dataset over SceneOpsDataset
directly: SequenceSampler.get(index) is async (it may read Parquet through
ArtifactStore), while torch.utils.data.Dataset.__getitem__ is a synchronous
contract, called from worker processes DataLoader forks/spawns when
num_workers > 0. Bridging that mismatch inside __getitem__ with
asyncio.run(...) either creates and tears down an event loop on every
single sample (slow, and raises RuntimeError outright if __getitem__ is
ever called from a thread/context that already has a running event loop --
e.g. an async training loop, a notebook, num_workers=0 inside an async
caller) or requires a second, hidden async-runtime-management layer this
request explicitly says not to build. Requiring explicit materialization
first (materialize_sequences, see numpy_adapter.py) moves every async
storage call out of __getitem__ entirely: SceneOpsTorchDataset then holds
only plain NumPy arrays + primitives, is trivially picklable, and its
__getitem__ never awaits, blocks on I/O, or touches asyncio at all -- safe
under any DataLoader num_workers/start-method combination.

SceneOpsTorchDataset never queries Parquet, knows ArtifactStore, performs
curation, resolves feature schemas, recalculates windows, changes
missing-value semantics, or performs augmentation -- every one of those
stays owned by SceneOpsDataset/SequenceSampler, already applied before this
class is ever constructed.
"""

from __future__ import annotations

from typing import Any

import torch
from torch.utils.data import Dataset

from .numpy_adapter import NumPySequenceSample


class SceneOpsTorchDataset(Dataset):
    """Synchronous, map-style torch Dataset over an already-materialized
    ``list[NumPySequenceSample]`` (SceneOps V2 Request 2.7D §6/§7).

    Construct via ``SceneOpsTorchDataset(materialized)`` where
    ``materialized`` came from ``await materialize_sequences(sampler, ...)``
    -- this class never materializes anything itself and holds no reference
    to a SceneOpsDataset/SequenceSampler/ArtifactStore.
    """

    def __init__(self, samples: list[NumPySequenceSample]) -> None:
        self._samples = samples

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, index: int) -> dict[str, Any]:
        sample = self._samples[index]
        return {
            "episode_id": sample.episode_ref.episode_id,
            "aligned_artifact_checksum": sample.episode_ref.aligned_artifact_checksum,
            "start_step": sample.start_step,
            "horizon": sample.horizon,
            "timestamps_us": torch.from_numpy(sample.timestamps_us),
            "observation": torch.from_numpy(sample.observation),
            "action": torch.from_numpy(sample.action),
        }


__all__ = ["SceneOpsTorchDataset"]
