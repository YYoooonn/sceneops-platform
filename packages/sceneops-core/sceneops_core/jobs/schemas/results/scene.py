from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class IngestScenesJobResult(BaseJobResult):
    """LEGACY producer output: pre-canonical scene manifests."""

    scene_ids: list[str] = Field(default_factory=list)
    legacy_scene_manifest_uris: list[str] = Field(default_factory=list)

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class BuildScenesJobResult(BaseJobResult):
    """LEGACY producer output: pre-canonical scene manifests."""

    raw_log_id: str | None = None

    scene_ids: list[str] = Field(default_factory=list)
    legacy_scene_manifest_uris: list[str] = Field(default_factory=list)

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    scene_segment_index_uri: str | None = None

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

    # Sample grouping report — populated by SampleGrouper across all built segments.
    # Non-zero drop/warn counts appear when required_channels triggers missing_channel_policy.
    sample_count_before_filtering: int = 0
    sample_count_after_filtering: int = 0
    dropped_sample_count: int = 0
    warned_sample_count: int = 0
    samples_with_missing_channels_count: int = 0
    missing_channel_counts_by_channel: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class BuildDatasetManifestJobResult(BaseJobResult):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str

    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0

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
    keyframe_count: int = 0
    observation_count: int = 0
    annotation_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)

    profile_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RegisterScenesJobResult(BaseJobResult):
    """``scene_ids`` / ``manifest_artifact_ids`` are the canonical members
    of the registered scope after commit, each at its current revision: the
    input units for an external registration, the whole recording scope for
    a recording one (which, when its fingerprint is unchanged, is the
    already-registered set). ``removed_scene_ids`` are records of a replaced
    recording scope that the new set no longer contains."""

    dataset_id: str
    dataset_version: str

    scene_ids: list[str] = Field(default_factory=list)
    manifest_artifact_ids: list[str] = Field(default_factory=list)

    created_scene_ids: list[str] = Field(default_factory=list)
    replaced_scene_ids: list[str] = Field(default_factory=list)
    unchanged_scene_ids: list[str] = Field(default_factory=list)
    removed_scene_ids: list[str] = Field(default_factory=list)

    registered_scene_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class BuildSceneIndexJobResult(BaseJobResult):
    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_index_uri: str

    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0

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
