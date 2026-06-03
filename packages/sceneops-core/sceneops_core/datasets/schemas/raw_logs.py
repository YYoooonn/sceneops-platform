from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import (
    RawLogSourceFormat,
    SceneBuildPolicyType,
    SensorFrameRole,
    SensorModality,
)


# ── Predicate configs (sceneops-core owns the schema; worker owns the logic) ──


class ObjectNeighborhoodPredicateConfig(SceneOpsBaseModel):
    type: Literal["object_neighborhood"] = "object_neighborhood"
    required_categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    max_distance_m: float | None = None
    min_count: int = 1
    max_count: int | None = None


class EgoKinematicPredicateConfig(SceneOpsBaseModel):
    type: Literal["ego_kinematic"] = "ego_kinematic"
    speed_min_kmh: float | None = None
    speed_max_kmh: float | None = None
    decel_min_ms2: float | None = None


class AndPredicateConfig(SceneOpsBaseModel):
    type: Literal["and"] = "and"
    predicates: list[AnyPredicateConfig] = Field(default_factory=list)


class OrPredicateConfig(SceneOpsBaseModel):
    type: Literal["or"] = "or"
    predicates: list[AnyPredicateConfig] = Field(default_factory=list)


AnyPredicateConfig = Annotated[
    Union[
        ObjectNeighborhoodPredicateConfig,
        EgoKinematicPredicateConfig,
        AndPredicateConfig,
        OrPredicateConfig,
    ],
    Field(discriminator="type"),
]

AndPredicateConfig.model_rebuild()
OrPredicateConfig.model_rebuild()


class ScenarioMiningConfig(SceneOpsBaseModel):
    predicate: AnyPredicateConfig
    pre_event_seconds: float = 3.0
    post_event_seconds: float = 7.0
    min_gap_between_scenes_seconds: float = 5.0


class TimeRange(SceneOpsBaseModel):
    start_timestamp_us: int
    end_timestamp_us: int


class RawSensorFrameManifest(SceneOpsBaseModel):
    frame_id: str
    timestamp_us: int

    channel: str
    modality: SensorModality = SensorModality.UNKNOWN
    role: SensorFrameRole = SensorFrameRole.UNKNOWN

    uri: str

    # optional source-specific references
    source_sample_id: str | None = None
    source_scene_id: str | None = None
    ego_pose_ref: str | None = None
    calibration_ref: str | None = None
    annotation_refs: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class RawLogManifest(SceneOpsBaseModel):
    raw_log_id: str

    dataset_id: str
    dataset_version: str
    dataset_type: str

    source_format: RawLogSourceFormat
    root_uri: str

    time_range: TimeRange | None = None
    channels: list[str] = Field(default_factory=list)
    frame_count: int = 0

    frame_index_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RawLogFrameIndex(SceneOpsBaseModel):
    raw_log_id: str
    dataset_id: str
    dataset_version: str

    frames: list[RawSensorFrameManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class SceneBuildPolicy(SceneOpsBaseModel):
    type: SceneBuildPolicyType = SceneBuildPolicyType.FIXED_WINDOW

    # FIXED_WINDOW / GAP_BASED
    window_seconds: float = 20.0
    stride_seconds: float | None = None

    required_channels: list[str] = Field(
        default_factory=lambda: ["CAM_FRONT", "LIDAR_TOP"]
    )
    max_timestamp_gap_ms: int = 500
    min_frame_count: int = 2

    split_on_missing_required_channel: bool = True
    split_on_timestamp_gap: bool = False
    split_on_source_scene_boundary: bool = False

    # SCENARIO_MINING
    mining: ScenarioMiningConfig | None = None

    metadata: JsonDict = Field(default_factory=dict)


class SceneSegmentManifest(SceneOpsBaseModel):
    segment_id: str
    raw_log_id: str

    start_timestamp_us: int
    end_timestamp_us: int

    frame_ids: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)

    policy: SceneBuildPolicy
    quality_summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class SceneSegmentIndex(SceneOpsBaseModel):
    raw_log_id: str
    dataset_id: str
    dataset_version: str

    segments: list[SceneSegmentManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class SceneSegmentListResponse(SceneOpsBaseModel):
    segments: list[SceneSegmentManifest]
    count: int
    total: int
