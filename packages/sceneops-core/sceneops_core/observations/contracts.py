from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

ObservationIngestionRequestT = TypeVar(
    "ObservationIngestionRequestT",
    contravariant=True,
)
ObservationIngestionResultT = TypeVar(
    "ObservationIngestionResultT",
    covariant=True,
)

ObservationIndexRequestT = TypeVar(
    "ObservationIndexRequestT",
    contravariant=True,
)
ObservationIndexResultT = TypeVar(
    "ObservationIndexResultT",
    covariant=True,
)


@runtime_checkable
class ObservationIngestor(
    Protocol,
    Generic[ObservationIngestionRequestT, ObservationIngestionResultT],
):
    """Port-like contract for ingesting raw observation sources.

    Implementations may ingest nuScenes, ROS bags, custom robot logs,
    or simulator outputs into raw log manifests.
    """

    @property
    def source_format(self) -> str:
        """Stable source format identifier, e.g. nuscenes or rosbag."""

    async def run(
        self,
        request: ObservationIngestionRequestT,
    ) -> ObservationIngestionResultT:
        """Ingest a raw observation source and return an observation result."""


@runtime_checkable
class ObservationIndexer(
    Protocol,
    Generic[ObservationIndexRequestT, ObservationIndexResultT],
):
    """Port-like contract for indexing raw observation frames."""

    @property
    def indexer_id(self) -> str:
        """Stable indexer identifier, e.g. nuscenes-frame-indexer."""

    async def run(
        self,
        request: ObservationIndexRequestT,
    ) -> ObservationIndexResultT:
        """Build a raw log frame index from an observation manifest."""
