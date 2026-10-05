from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.schemas.records import EpisodeRecord


class EpisodeDetailResponse(SceneOpsBaseModel):
    episode: EpisodeRecord


class EpisodeListResponse(SceneOpsBaseModel):
    episodes: list[EpisodeRecord]
    count: int


class EpisodeManifestResponse(SceneOpsBaseModel):
    """The canonical EpisodeManifest revision the Episode currently points
    to, verified against its pinned checksum before it is returned."""

    episode_id: str
    manifest_artifact_id: str
    manifest_checksum: str
    manifest: dict[str, Any]


# ── Episode quality summary ──────────────────────────────────────────────────
#
# Only run records that assessed the Episode's current manifest revision
# count (ADR-007 §13.4).


class EpisodeQualityReadiness(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class EpisodeQualityCounts(SceneOpsBaseModel):
    observation_count: int = 0
    state_count: int = 0
    action_count: int = 0
    event_count: int = 0


class EpisodeValidationQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    validation_status: str | None = None
    should_block_pipeline: bool = False
    blocking_issue_count: int | None = None
    warning_count: int | None = None
    issue_count: int | None = None
    report_uri: str | None = None


class EpisodeProfileQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    observation_count: int | None = None
    state_count: int | None = None
    action_count: int | None = None
    event_count: int | None = None
    window_duration_ns: int | None = None
    profile_report_uri: str | None = None


class EpisodeQualityResponse(SceneOpsBaseModel):
    episode_id: str
    dataset_id: str
    dataset_version: str
    manifest_artifact_id: str
    manifest_checksum: str

    counts: EpisodeQualityCounts = Field(default_factory=EpisodeQualityCounts)
    validation: EpisodeValidationQualitySummary | None = None
    profile: EpisodeProfileQualitySummary | None = None

    readiness: EpisodeQualityReadiness = EpisodeQualityReadiness.UNKNOWN
    blocking_reasons: list[str] = Field(default_factory=list)
