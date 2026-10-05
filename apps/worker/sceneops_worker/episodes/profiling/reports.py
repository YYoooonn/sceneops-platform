from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class EpisodeStreamProfile(SceneOpsBaseModel):
    topic: str
    role: str
    source_clock: str
    count: int = 0
    first_timestamp_ns: int | None = None
    last_timestamp_ns: int | None = None
    # Occurrences that share their timestamp with an earlier one of the same
    # stream; kept as separate occurrences in the canonical Episode.
    duplicate_timestamp_count: int = 0


class EpisodeProfileResult(SceneOpsBaseModel):
    episode_id: str

    observation_count: int = 0
    state_count: int = 0
    action_count: int = 0
    event_count: int = 0

    observation_topics: list[str] = Field(default_factory=list)
    state_topics: list[str] = Field(default_factory=list)
    action_topics: list[str] = Field(default_factory=list)
    event_topics: list[str] = Field(default_factory=list)

    window_clock: str
    window_duration_ns: int

    streams: list[EpisodeStreamProfile] = Field(default_factory=list)
