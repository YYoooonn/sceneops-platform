from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType


class EpisodeValidationRunRecord(BaseRunRecord):
    """Append-only Episode validation execution history (SceneOps V2 Request 17).

    Mirrors SceneValidationRunRecord's shape — one row per validate_episode
    invocation, scoped to either a whole job (episode_id=None, aggregate
    across every episode the job checked) or a single episode
    (episode_id set) — same job-level/per-item split validate_scene uses.
    """

    type: RunType = RunType.EPISODE_VALIDATION

    episode_id: str | None = None
    episode_manifest_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    validation_status: str | None = None
    should_block_pipeline: bool = False

    validation_report_uri: str | None = None

    checked_episode_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    summary: JsonDict = Field(default_factory=dict)


class EpisodeProfileRunRecord(BaseRunRecord):
    """Append-only Episode profile execution history (SceneOps V2 Request 17)."""

    type: RunType = RunType.EPISODE_PROFILE

    episode_id: str | None = None
    episode_manifest_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    profile_report_uri: str | None = None

    checked_episode_count: int | None = None

    frame_count: int | None = None
    observation_count: int | None = None
    action_count: int | None = None

    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)

    control_frequency_hz: float | None = None
    duration_us: int | None = None

    task: str | None = None
    outcome: str | None = None
    mission_id: str | None = None

    summary: JsonDict = Field(default_factory=dict)
