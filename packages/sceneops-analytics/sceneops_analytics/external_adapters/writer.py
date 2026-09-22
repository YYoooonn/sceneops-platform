"""ExternalDatasetWriter: the stateful per-export write session
(SceneOps V2 Request 3.1A).

Request 3.1's original contract called ``ExternalDatasetAdapter.write_episode``
directly on the adapter itself, once per Episode, with no notion of a target
that needs setting up before the first Episode or finished after the last.
That assumes an external dataset is one independently writable file per
Episode -- true for nothing this platform actually targets: LeRobot writes a
whole dataset directory (info.json, per-episode Parquet shards, and global
stats computed *across* every episode); RLDS writes sharded TFRecord files
plus a dataset-level features spec. Both need something opened before the
first Episode and something finalized only after the last.

ExternalDatasetWriter is that lifecycle, isolated from ExternalDatasetAdapter
so per-export mutable state (open files, shard counters, accumulated stats)
never leaks across separate ``export()`` calls on the same adapter instance
-- ``ExternalDatasetAdapter.open_writer()`` creates a fresh one per call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .schemas import ExternalEpisode


class ExternalDatasetWriter(ABC):
    """One export's write session for one external-format target (SceneOps
    V2 Request 3.1A). ``ExternalDatasetAdapter.export()`` guarantees exactly
    this sequence, in order, for every writer it opens::

        initialize() -> write_episode() x N (export order) -> finalize()

    ``write_episode()`` is called once per exported Episode; a concrete
    writer decides internally how many underlying files/shards that
    requires -- the shared contract makes no assumption there. ``finalize()``
    runs only once every ``write_episode()`` call has succeeded, and is
    export()'s responsibility to invoke -- a caller of ``export()`` never
    needs to remember a separate cleanup step. If any step raises, export()
    lets the exception propagate immediately and does not call the next step
    (in particular, a failed ``write_episode()`` means ``finalize()`` is
    never called for that export).

    None of ExternalEpisode/ExternalStep/ExternalExportReport's shape
    depends on this lifecycle -- a writer only controls *how* episodes reach
    the target format, never what an episode or the export report contains.
    Request 3.1A defines no concrete implementation.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Called exactly once, before any write_episode() call. Creates
        whatever target-format structure/resources this export needs
        (dataset directory, container file, shard index, ...)."""

    @abstractmethod
    async def write_episode(self, episode: ExternalEpisode) -> None:
        """Called once per exported Episode, in export order, strictly
        between initialize() and finalize()."""

    @abstractmethod
    async def finalize(self) -> None:
        """Called exactly once, only after every write_episode() call for
        this export has succeeded. Flushes/closes resources and writes
        whatever aggregate metadata the target format can only compute once
        every Episode is known (global stats, a shard index, ...)."""


__all__ = ["ExternalDatasetWriter"]
