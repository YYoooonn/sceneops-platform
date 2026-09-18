from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.schemas.records import EpisodeRecord


class EpisodeDetailResponse(SceneOpsBaseModel):
    episode: EpisodeRecord


class EpisodeListResponse(SceneOpsBaseModel):
    episodes: list[EpisodeRecord]
    count: int


# ── Episode quality summary response (SceneOps V2 Request 17) ────────────────
#
# Mirrors the Scene quality response shape (SceneQualityResponse) but keeps
# Episode-specific semantics explicit rather than reusing Scene's schemas —
# Episode's readiness derivation has no Scene-domain concepts (no
# ground-truth/selectable_for_detection notion; that's Scene's own
# detection-training framing, not something the Episode domain model
# currently supports or needs).


class EpisodeQualityReadiness(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class EpisodeQualityCounts(SceneOpsBaseModel):
    frame_count: int = 0
    observation_count: int | None = None
    action_count: int | None = None


class EpisodeValidationQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    validation_status: str | None = None
    should_block_pipeline: bool = False
    checked_episode_count: int | None = None
    blocking_issue_count: int | None = None
    warning_count: int | None = None
    issue_count: int | None = None
    report_uri: str | None = None


class EpisodeProfileQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    frame_count: int | None = None
    observation_count: int | None = None
    action_count: int | None = None
    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)
    control_frequency_hz: float | None = None
    duration_us: int | None = None
    task: str | None = None
    outcome: str | None = None
    profile_report_uri: str | None = None


class EpisodeQualityResponse(SceneOpsBaseModel):
    episode_id: str
    dataset_id: str | None = None
    dataset_version: str | None = None
    status: str

    counts: EpisodeQualityCounts = Field(default_factory=EpisodeQualityCounts)
    validation: EpisodeValidationQualitySummary | None = None
    profile: EpisodeProfileQualitySummary | None = None

    readiness: EpisodeQualityReadiness = EpisodeQualityReadiness.UNKNOWN
    blocking_reasons: list[str] = Field(default_factory=list)
