from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas import DatasetType
from sceneops_core.observations.schemas import RawLogSourceFormat, RawLogSourceType
from sceneops_core.scenes.legacy import SampleGroupingConfig, SceneSegmentationConfig

from .base import BaseJobParams


class SceneKeyframeValidationConfig(SceneOpsBaseModel):
    """Validation options applied to each source-defined keyframe group."""

    validate_keyframes: bool = True
    block_on_keyframe_missing_channels: bool = False


class IngestScenesJobParams(BaseJobParams):
    """LEGACY producer: import existing scene-aware datasets as pre-canonical
    ``LegacySceneManifest`` artifacts, which canonical registration does not
    accept.

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
    source_format_version: str | None = None

    source_scene_ids: list[str] | None = None
    max_source_scenes: int | None = None

    output_scene_root_uri: str | None = None

    mode: str = "upsert"

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_source_format_version_for_nuscenes(self) -> "IngestScenesJobParams":
        if (
            self.source_format == DatasetType.NUSCENES
            and not self.source_format_version
        ):
            raise ValueError(
                "source_format_version is required when source_format=nuscenes "
                "-- it is the nuScenes SDK's own on-disk version (e.g. "
                "'v1.0-mini'), never SceneOps' canonical dataset_version"
            )
        return self


class BuildScenesJobParams(BaseJobParams):
    """LEGACY producer: raw logs to pre-canonical ``LegacySceneManifest``
    artifacts, which canonical registration does not accept."""

    raw_log_id: str | None = None
    raw_log_manifest_uri: str | None = None
    raw_log_frame_index_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    # Raw-log source classification
    source_type: RawLogSourceType | None = None
    source_format: RawLogSourceFormat | None = None
    # SceneOps V2 Request 3.2B: same split as IngestScenesJobParams above --
    # the external source format's own version identity (e.g. nuScenes'
    # "v1.0-mini"), never `dataset_version`. Required whenever source_type
    # needs one (currently: NUSCENES_RAW_LOG_MOCK); RosbagAdapter ignores
    # it. Request 3.2B.1 removed the `source_format_version or
    # dataset_version` fallback that briefly existed here -- no backward
    # compatibility with that overloaded behavior is preserved.
    source_format_version: str | None = None
    # URI of the actual sensor record files (rosbag, MCAP, etc.) for real adapters.
    # Not used as a raw source root; raw source root comes from DatasetVersionRecord.
    records_uri: str | None = None

    segmentation: SceneSegmentationConfig = Field(
        default_factory=SceneSegmentationConfig
    )

    sampling: SampleGroupingConfig = Field(default_factory=SampleGroupingConfig)

    max_source_sequences: int | None = None
    max_built_scenes: int | None = None

    build_assets: bool = True

    output_scene_root_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_source_format_version_for_nuscenes_raw_log(
        self,
    ) -> "BuildScenesJobParams":
        if (
            self.source_type == RawLogSourceType.NUSCENES_RAW_LOG_MOCK
            and not self.source_format_version
        ):
            raise ValueError(
                "source_format_version is required when "
                "source_type=nuscenes_raw_log_mock -- it is the nuScenes "
                "SDK's own on-disk version (e.g. 'v1.0-mini'), never "
                "SceneOps' canonical dataset_version"
            )
        return self


class BuildDatasetManifestJobParams(BaseJobParams):
    """Derive a dataset manifest from the registered SceneRecords of one
    DatasetVersion."""

    dataset_id: str
    dataset_version: str

    metadata: JsonDict = Field(default_factory=dict)


class ValidateSceneJobParams(BaseJobParams):
    """Validate registered Scenes. Each Scene is assessed at the manifest
    revision its record points to when the job reads it, and the per-scene
    run record pins that revision."""

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_ids: list[str] = Field(default_factory=list)

    require_target_channels: list[str] = Field(default_factory=list)

    keyframe_validation: SceneKeyframeValidationConfig = Field(
        default_factory=SceneKeyframeValidationConfig
    )

    metadata: JsonDict = Field(default_factory=dict)


class ProfileSceneJobParams(BaseJobParams):
    """Profile registered Scenes at their current revision; see
    ``ValidateSceneJobParams``."""

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_ids: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class RegisterScenesJobParams(BaseJobParams):
    """Canonical Scene registration (ADR-007 §17, §18).

    ``manifest_artifact_ids`` name SCENE_MANIFEST ArtifactRecords; the
    registrar re-reads and verifies their bytes. A recording-derived input
    must be the complete unit set of one recording scope.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    manifest_artifact_ids: list[str] = Field(min_length=1)

    replace: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class BuildSceneIndexJobParams(BaseJobParams):
    dataset_id: str | None = None
    dataset_version: str | None = None

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
