from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class IngestScenesJobResult(BaseJobResult):
    scene_ids: list[str] = Field(default_factory=list)
    scene_manifest_uris: list[str] = Field(default_factory=list)

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class BuildScenesJobResult(BaseJobResult):
    raw_log_id: str | None = None

    scene_ids: list[str] = Field(default_factory=list)
    scene_manifest_uris: list[str] = Field(default_factory=list)

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    scene_segment_index_uri: str | None = None
    world_state_uris: list[str] = Field(default_factory=list)

    # Raw-log provenance
    raw_log_manifest_uri: str | None = None
    raw_log_frame_index_uri: str | None = None
    records_uri: str | None = None
    source_type: str | None = None
    source_format: str | None = None
    observation_count: int = 0
    channels: list[str] = Field(default_factory=list)
    segmentation_strategy: str | None = None
    sampling_strategy: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class BuildDatasetManifestJobResult(BaseJobResult):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class ValidateSceneJobResult(BaseJobResult):
    status: str = "ready"
    should_block_pipeline: bool = False

    checked_scene_count: int = 0
    issue_count: int = 0

    validation_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ProfileSceneJobResult(BaseJobResult):
    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)
    asset_summary: JsonDict = Field(default_factory=dict)
    world_state_summary: JsonDict = Field(default_factory=dict)

    profile_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RegisterSceneJobResult(BaseJobResult):
    # Singular (backward-compatible)
    scene_id: str | None = None
    scene_manifest_uri: str | None = None

    # Bulk
    scene_ids: list[str] = Field(default_factory=list)
    scene_manifest_uris: list[str] = Field(default_factory=list)
    registered_scene_count: int = 0

    registered: bool = True

    metadata: JsonDict = Field(default_factory=dict)


class BuildSceneIndexJobResult(BaseJobResult):
    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_index_uri: str

    scene_manifest_uris: list[str] = Field(default_factory=list)
    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class CompareScenesJobResult(BaseJobResult):
    comparison_run_id: str | None = None
    comparison_report_uri: str | None = None

    summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class AutoLabelSceneJobResult(BaseJobResult):
    scene_id: str | None = None

    output_scene_manifest_uri: str | None = None
    output_label_uri: str | None = None

    annotation_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class ExportScenePackageJobResult(BaseJobResult):
    package_uri: str

    package_type: str = "reconstruction"
    output_format: str = "sceneops"

    metadata: JsonDict = Field(default_factory=dict)
