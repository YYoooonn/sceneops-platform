from __future__ import annotations


class PipelineContextKey:
    DATASET_ID = "dataset_id"
    DATASET_VERSION = "dataset_version"
    DATASET_MANIFEST_URI = "dataset_manifest_uri"
    DATASET_TYPE = "dataset_type"
    DATASET_STATUS = "dataset_status"

    SCENE_COUNT = "scene_count"
    SAMPLE_COUNT = "sample_count"
    ANNOTATION_COUNT = "annotation_count"

    ISSUE_COUNT = "issue_count"
    ERROR_COUNT = "error_count"
    WARNING_COUNT = "warning_count"

    VALIDATION_RUN_ID = "validation_run_id"
    VALIDATION_REPORT_URI = "validation_report_uri"
    VALIDATION_STATUS = "validation_status"
    VALIDATION_SCOPE = "validation_scope"
    SHOULD_BLOCK_PIPELINE = "should_block_pipeline"

    VALIDATED_SCENE_COUNT = "validated_scene_count"
    VALIDATED_SAMPLE_COUNT = "validated_sample_count"

    VALIDATION_ISSUE_COUNT = "validation_issue_count"
    VALIDATION_ERROR_COUNT = "validation_error_count"
    VALIDATION_WARNING_COUNT = "validation_warning_count"

    MISSING_SCENE_COUNT = "missing_scene_count"
    MISSING_SAMPLE_COUNT = "missing_sample_count"
    MISSING_CHANNEL_COUNT = "missing_channel_count"
    MISSING_ARTIFACT_COUNT = "missing_artifact_count"

    PROFILE_RUN_ID = "profile_run_id"
    PROFILE_REPORT_URI = "profile_report_uri"
    PROFILE_SCENE_COUNT = "profile_scene_count"
    PROFILE_SAMPLE_COUNT = "profile_sample_count"
    PROFILE_SUMMARY = "profile_summary"
    PROFILE_OBSERVED_CHANNELS = "profile_observed_channels"
    PROFILE_MISSING_REQUIRED_CHANNEL_COUNT = "profile_missing_required_channel_count"
    PROFILE_SENSOR_COVERAGE_RATIO = "profile_sensor_coverage_ratio"
    PROFILE_EMPTY_ANNOTATION_SAMPLE_COUNT = "profile_empty_annotation_sample_count"
    PROFILE_EMPTY_ANNOTATION_SAMPLE_RATIO = "profile_empty_annotation_sample_ratio"

    OBSERVED_CHANNELS = "observed_channels"
    OBSERVED_CHANNEL_COUNT = "observed_channel_count"
    MISSING_REQUIRED_CHANNEL_COUNT = "missing_required_channel_count"
    SENSOR_COVERAGE_RATIO = "sensor_coverage_ratio"
    EMPTY_ANNOTATION_SAMPLE_COUNT = "empty_annotation_sample_count"
    EMPTY_ANNOTATION_SAMPLE_RATIO = "empty_annotation_sample_ratio"

    MODEL_ID = "model_id"
    MODE_VERSION = "model_version"

    INFERENCE_RUN_ID = "inference_run_id"
    INFERENCE_BACKEND = "inference_backend"

    PREDICTION_MODEL_ID = "prediction_model_id"
    PREDICTION_MODEL_VERSION = "prediction_model_version"
    PREDICTION_MANIFEST_URI = "prediction_manifest_uri"
    PREDICTION_SAMPLE_COUNT = "prediction_sample_count"

    EVALUATION_RUN_ID = "evaluation_run_id"
    EVALUATION_REPORT_URI = "evaluation_report_uri"
    EVALUATION_MANIFEST_URI = "evaluation_manifest_uri"

    EVALUATION_MODEL_ID = "evaluation_model_id"
    EVALUATION_MODEL_VERSION = "evaluation_model_version"

    EVALUATION_METRICS = "evaluation_metrics"
    EVALUATION_SAMPLE_COUNT = "evaluation_sample_count"
