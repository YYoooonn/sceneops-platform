"""Episode domain logic that needs no worker runtime: building Episodes from a registered
recording, validating and profiling an Episode manifest, and the Episode artifact layout.

Registration, resolution and the Jobs that run them are in ``apps/worker``."""

from .artifacts import EpisodeArtifactStore

__all__ = ["EpisodeArtifactStore"]
