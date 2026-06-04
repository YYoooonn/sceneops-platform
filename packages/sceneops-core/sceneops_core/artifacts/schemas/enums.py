from __future__ import annotations

from enum import StrEnum


class ArtifactBackend(StrEnum):
    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"


class ArtifactKind(StrEnum):
    # Observation-level
    RAW_LOG_MANIFEST = "raw_log_manifest"
    RAW_LOG_FRAME_INDEX = "raw_log_frame_index"
    RAW_SENSOR_FRAME = "raw_sensor_frame"

    # Scene-level
    SCENE_INDEX = "scene_index"
    SCENE_MANIFEST = "scene_manifest"
    SCENE_SAMPLE_MANIFEST = "scene_sample_manifest"
    SCENE_SEGMENT_INDEX = "scene_segment_index"
    WORLD_STATE_MANIFEST = "world_state_manifest"
    SCENE_PACKAGE = "scene_package"

    # Dataset-level
    DATASET_MANIFEST = "dataset_manifest"
    DATASET_VALIDATION_REPORT = "dataset_validation_report"
    DATASET_PROFILE_REPORT = "dataset_profile_report"
    DISTRIBUTION_REPORT = "distribution_report"

    # Scenario-level
    SCENARIO_SET_MANIFEST = "scenario_set_manifest"
    SCENARIO_MINING_REPORT = "scenario_mining_report"
    SCENARIO_READINESS_REPORT = "scenario_readiness_report"

    # Inference / evaluation
    PREDICTION_MANIFEST = "prediction_manifest"
    PREDICTIONS_ROOT = "predictions_root"
    EVALUATION_MANIFEST = "evaluation_manifest"
    METRICS = "metrics"

    # Auto-label
    AUTO_LABEL_MANIFEST = "auto_label_manifest"
    AUTO_LABEL_REPORT = "auto_label_report"

    # Model
    MODEL_ARTIFACT = "model_artifact"
    MODEL_CONFIG = "model_config"

    OTHER = "other"
