from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.scenes.schemas.records import SceneRecord


class SceneDetailResponse(SceneOpsBaseModel):
    scene: SceneRecord


class SceneListResponse(SceneOpsBaseModel):
    scenes: list[SceneRecord]
    count: int


# ── Scene quality summary response ────────────────────────────────────────────


class SceneQualityReadiness(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class SceneQualityCounts(SceneOpsBaseModel):
    sample_count: int = 0
    frame_count: int = 0
    annotation_count: int | None = None


class SceneGroundTruthQualitySummary(SceneOpsBaseModel):
    has_ground_truth: bool = False
    annotation_count: int | None = None
    ground_truth_source: str | None = None


class SceneValidationQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    validation_status: str | None = None
    should_block_pipeline: bool = False
    checked_sample_count: int | None = None
    checked_frame_count: int | None = None
    blocking_issue_count: int | None = None
    warning_count: int | None = None
    issue_count: int | None = None
    report_uri: str | None = None


class SceneProfileQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    sample_count: int | None = None
    frame_count: int | None = None
    annotation_count: int | None = None
    observed_channels: list[str] = Field(default_factory=list)
    profile_report_uri: str | None = None


class SceneQualityResponse(SceneOpsBaseModel):
    scene_id: str
    dataset_id: str | None = None
    dataset_version: str | None = None
    status: str

    counts: SceneQualityCounts = Field(default_factory=SceneQualityCounts)
    ground_truth: SceneGroundTruthQualitySummary = Field(
        default_factory=SceneGroundTruthQualitySummary
    )
    validation: SceneValidationQualitySummary | None = None
    profile: SceneProfileQualitySummary | None = None

    readiness: SceneQualityReadiness = SceneQualityReadiness.UNKNOWN
    selectable_for_detection: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
