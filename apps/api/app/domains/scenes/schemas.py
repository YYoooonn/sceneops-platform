from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.scenes import SceneReadiness
from sceneops_core.scenes.schemas.records import SceneRecord


class SceneDetailResponse(SceneOpsBaseModel):
    scene: SceneRecord


class SceneListResponse(SceneOpsBaseModel):
    scenes: list[SceneRecord]
    count: int


# ── Scene quality summary response ────────────────────────────────────────────
#
# Quality is reported for the Scene's current manifest revision only: the
# validation / profile summaries come from run records that pinned exactly
# that revision, and readiness is derived from them, never stored. Labels and
# detection selectability are not Scene quality: they belong to derived
# label sets and sample views (ADR-007 §33).

SceneQualityReadiness = SceneReadiness


class SceneQualityCounts(SceneOpsBaseModel):
    keyframe_count: int = 0
    observation_count: int = 0


class SceneValidationQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    manifest_artifact_id: str | None = None
    validation_status: str | None = None
    should_block_pipeline: bool = False
    checked_observation_count: int | None = None
    checked_keyframe_count: int | None = None
    blocking_issue_count: int | None = None
    warning_count: int | None = None
    issue_count: int | None = None
    report_uri: str | None = None


class SceneProfileQualitySummary(SceneOpsBaseModel):
    run_id: str
    status: str
    manifest_artifact_id: str | None = None
    observation_count: int | None = None
    keyframe_count: int | None = None
    observed_channels: list[str] = Field(default_factory=list)
    profile_report_uri: str | None = None


class SceneQualityResponse(SceneOpsBaseModel):
    scene_id: str
    dataset_id: str
    dataset_version: str
    manifest_artifact_id: str
    manifest_checksum: str

    counts: SceneQualityCounts = Field(default_factory=SceneQualityCounts)
    validation: SceneValidationQualitySummary | None = None
    profile: SceneProfileQualitySummary | None = None

    readiness: SceneReadiness = SceneReadiness.UNKNOWN
