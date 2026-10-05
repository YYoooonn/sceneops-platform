from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class SceneVersionSummary(SceneOpsBaseModel):
    """Scene-domain state on one DatasetVersion.

    ``scene_count``, ``keyframe_count``, ``observation_count`` and
    ``observed_channels`` are cached projections of canonical Scene
    membership. The Scene registrar is their only writer: it recomputes
    them from SceneRecord rows under the DatasetVersion row lock, in the
    same transaction as the membership change (ADR-007 §16).

    Validation / profile results are not cached here; Scene readiness is
    derived from run records for each Scene's current manifest revision.

    A non-None ``scene`` on DatasetVersionRecord does not prove Scenes are
    registered; check ``scene_count`` or query SceneRecord rows.
    """

    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0
    observed_channels: list[str] = Field(default_factory=list)

    def is_unset(self) -> bool:
        """True if every field is still at its untouched default. Says
        nothing about whether Scene data exists."""
        return self == SceneVersionSummary()


class EpisodeVersionSummary(SceneOpsBaseModel):
    """Episode-domain state for one DatasetVersion. ``episode_count`` is
    recomputed from canonical membership by the Episode registrar
    (REGISTER_EPISODES) only.

    Same caveat as SceneVersionSummary: non-None does not by itself prove
    Episode data exists — check ``episode_count > 0`` or query EpisodeRecord
    rows when presence actually matters.
    """

    episode_count: int = 0

    def is_unset(self) -> bool:
        return self == EpisodeVersionSummary()
