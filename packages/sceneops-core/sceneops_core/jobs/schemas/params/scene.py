from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetType
from sceneops_core.scenes.schemas import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneSegmentationConfig,
)

from .base import BaseJobParams


class IngestScenesJobParams(BaseJobParams):
    """Import existing scene-aware datasets into SceneOps scenes.

    Examples:
    - nuScenes scenes
    - Waymo segments
    - KITTI sequences
    - custom dataset scenes

    This job does not discover scenes from raw logs.
    It normalizes existing scene/sequence/sample structures into SceneOps SceneManifest.
    """

    dataset_id: str
    dataset_version: str

    source_format: DatasetType = DatasetType.NUSCENES
    source_root_uri: str

    source_scene_ids: list[str] | None = None
    max_scenes: int | None = None

    output_scene_root_uri: str | None = None

    mode: str = "upsert"

    metadata: JsonDict = Field(default_factory=dict)


class BuildScenesJobParams(BaseJobParams):
    """Build SceneOps scenes from raw logs or raw sensor streams.

    This job constructs scenes from raw observations using segmentation,
    sensor alignment, optional reconstruction, and asset/world-state creation.
    """

    raw_log_id: str | None = None
    raw_log_manifest_uri: str | None = None
    raw_log_frame_index_uri: str | None = None

    raw_root_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    segmentation: SceneSegmentationConfig = Field(
        default_factory=SceneSegmentationConfig
    )

    build_assets: bool = True
    build_world_state: bool = False

    origin_type: SceneOriginType = SceneOriginType.REAL
    generation_method: SceneGenerationMethod = SceneGenerationMethod.RAW_LOG

    output_scene_root_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class BuildDatasetManifestJobParams(BaseJobParams):
    """Build a dataset manifest from SceneOps scene manifests."""

    dataset_id: str
    dataset_version: str

    scene_manifest_uris: list[str] = Field(default_factory=list)

    output_manifest_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ValidateSceneJobParams(BaseJobParams):
    scene_id: str | None = None
    scene_manifest_uri: str | None = None

    scene_manifest_uris: list[str] = Field(default_factory=list)

    require_target_channels: list[str] = Field(default_factory=list)
    require_world_state: bool = False
    require_assets: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class ProfileSceneJobParams(BaseJobParams):
    scene_id: str | None = None
    scene_manifest_uri: str | None = None

    scene_manifest_uris: list[str] = Field(default_factory=list)

    profile_samples: bool = True
    profile_assets: bool = True
    profile_world_state: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class RegisterSceneJobParams(BaseJobParams):
    scene_id: str
    scene_manifest_uri: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    origin_type: SceneOriginType = SceneOriginType.GENERATED
    generation_method: SceneGenerationMethod = SceneGenerationMethod.UNKNOWN

    replace_existing: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class CompareScenesJobParams(BaseJobParams):
    source_scene_id: str | None = None
    source_scene_manifest_uri: str | None = None

    target_scene_id: str | None = None
    target_scene_manifest_uri: str | None = None

    compare_geometry: bool = True
    compare_annotations: bool = True
    compare_trajectories: bool = True
    compare_sensor_coverage: bool = True
    compare_world_state: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class AutoLabelSceneJobParams(BaseJobParams):
    scene_id: str | None = None
    scene_manifest_uri: str

    labeler_id: str = "default-auto-labeler"

    target_channels: list[str] = Field(default_factory=list)
    target_categories: list[str] = Field(default_factory=list)

    output_scene_manifest_uri: str | None = None
    output_label_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ExportScenePackageJobParams(BaseJobParams):
    scene_id: str | None = None
    scene_manifest_uri: str

    package_type: str = "reconstruction"
    output_format: str = "sceneops"
    output_root_uri: str | None = None

    include_assets: bool = True
    include_world_state: bool = True
    include_samples: bool = True
    include_annotations: bool = True

    metadata: JsonDict = Field(default_factory=dict)
