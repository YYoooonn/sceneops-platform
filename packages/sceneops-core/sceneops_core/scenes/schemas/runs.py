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


class SceneComparisonRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENE_COMPARISON

    source_scene_id: str | None = None
    source_scene_manifest_uri: str | None = None

    target_scene_id: str | None = None
    target_scene_manifest_uri: str | None = None

    comparison_report_uri: str | None = None

    geometry_summary: JsonDict = Field(default_factory=dict)
    annotation_summary: JsonDict = Field(default_factory=dict)
    trajectory_summary: JsonDict = Field(default_factory=dict)
    sensor_coverage_summary: JsonDict = Field(default_factory=dict)
    world_state_summary: JsonDict = Field(default_factory=dict)

    summary: JsonDict = Field(default_factory=dict)


class SceneReconstructionRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENE_RECONSTRUCTION

    raw_log_id: str | None = None
    raw_log_manifest_uri: str | None = None
    raw_log_frame_index_uri: str | None = None

    scene_id: str | None = None
    scene_manifest_uri: str | None = None
    world_state_manifest_uri: str | None = None

    asset_root_uri: str | None = None

    reconstructed_asset_count: int = 0

    reconstruction_summary: JsonDict = Field(default_factory=dict)


class ScenePackageExportRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENE_PACKAGE_EXPORT

    scene_id: str | None = None
    scene_manifest_uri: str

    package_type: str = "reconstruction"
    output_format: str = "sceneops"

    package_uri: str | None = None

    included_assets: bool = True
    included_world_state: bool = True
    included_samples: bool = True
    included_annotations: bool = True

    summary: JsonDict = Field(default_factory=dict)
