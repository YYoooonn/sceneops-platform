from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.schemas.records import (
    EpisodeRecord,
    EpisodeRevisionRecord,
)


class EpisodeDetailResponse(SceneOpsBaseModel):
    """An Episode unit and every registered revision of it; none is
    current."""

    episode: EpisodeRecord
    revisions: list[EpisodeRevisionRecord]


class EpisodeListResponse(SceneOpsBaseModel):
    episodes: list[EpisodeRecord]
    count: int


class EpisodeRevisionListResponse(SceneOpsBaseModel):
    """The Episode revisions a DatasetVersion pins, one per member unit."""

    episodes: list[EpisodeRevisionRecord]
    count: int


class EpisodeManifestResponse(SceneOpsBaseModel):
    """One canonical EpisodeManifest revision of the Episode, verified
    against its checksum before it is returned."""

    episode_id: str
    manifest_artifact_id: str
    manifest_checksum: str
    manifest: dict[str, Any]


# ── Episode quality summary ──────────────────────────────────────────────────
#
# Quality describes one revision of an Episode, named by its manifest
# checksum or by a DatasetVersion that pins it; only run records that
# assessed exactly that revision count.


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
    """``dataset_id`` / ``dataset_version`` are set when the revision was
    addressed through a DatasetVersion."""

    episode_id: str
    dataset_id: str | None = None
    dataset_version: str | None = None
    manifest_artifact_id: str
    manifest_checksum: str

    counts: EpisodeQualityCounts = Field(default_factory=EpisodeQualityCounts)
    validation: EpisodeValidationQualitySummary | None = None
    profile: EpisodeProfileQualitySummary | None = None

    readiness: EpisodeQualityReadiness = EpisodeQualityReadiness.UNKNOWN
    blocking_reasons: list[str] = Field(default_factory=list)
