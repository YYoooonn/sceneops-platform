from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class SceneVersionSummary(SceneOpsBaseModel):
    """Scene-domain summary of one DatasetVersion: the Scene revisions it
    pins, aggregated from their projections once, when the version is
    composed (``sceneops_core.datasets.composition.summarize_composition``).
    The version is immutable, so the summary never changes.

    Validation / profile results are not cached here; Scene readiness is
    derived from the run records of the revision each member pins.
    """

    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0
    observed_channels: list[str] = Field(default_factory=list)

    def is_unset(self) -> bool:
        """True if every field is at its default: the version pins no Scene."""
        return self == SceneVersionSummary()


class EpisodeVersionSummary(SceneOpsBaseModel):
    """Episode-domain summary of one DatasetVersion, computed once at
    composition like ``SceneVersionSummary``."""

    episode_count: int = 0

    def is_unset(self) -> bool:
        return self == EpisodeVersionSummary()
