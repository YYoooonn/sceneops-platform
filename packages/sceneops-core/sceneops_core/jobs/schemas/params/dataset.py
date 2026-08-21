from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobParams


class AutoLabelDatasetJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None

    labeler_id: str = "default-auto-labeler"

    target_scene_ids: list[str] | None = None
    target_channels: list[str] = Field(default_factory=list)
    target_categories: list[str] = Field(default_factory=list)

    output_dataset_id: str | None = None
    output_dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class CheckDistributionJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None

    group_by: list[str] = Field(
        default_factory=lambda: ["category", "channel", "scenario"]
    )

    compare_to_dataset_id: str | None = None
    compare_to_dataset_version: str | None = None
    compare_to_manifest_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ExportDatasetJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    scenario_set_uri: str | None = None

    output_format: str = "sceneops"
    output_root_uri: str | None = None

    dataset_kind: str | None = None
    split_key: str | None = None

    include_assets: bool = True
    include_annotations: bool = True
    include_predictions: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class ExportAnalyticsSnapshotJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    # None → export all known tables (scenes, samples, sensor_frames, annotations)
    tables: list[str] | None = None

    metadata: JsonDict = Field(default_factory=dict)
