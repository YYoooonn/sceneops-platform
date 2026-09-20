from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType


class SceneValidationRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENE_VALIDATION

    scene_id: str | None = None
    scene_manifest_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    validation_status: str | None = None
    should_block_pipeline: bool = False

    validation_report_uri: str | None = None

    checked_sample_count: int | None = None
    checked_frame_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    missing_channel_count: int | None = None
    missing_artifact_count: int | None = None

    summary: JsonDict = Field(default_factory=dict)


class SceneProfileRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENE_PROFILE

    scene_id: str | None = None
    scene_manifest_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    profile_report_uri: str | None = None

    sample_count: int | None = None
    frame_count: int | None = None
    asset_count: int | None = None
    annotation_count: int | None = None

    observed_channels: list[str] = Field(default_factory=list)

    asset_summary: JsonDict = Field(default_factory=dict)
    world_state_summary: JsonDict = Field(default_factory=dict)
    annotation_summary: JsonDict = Field(default_factory=dict)
