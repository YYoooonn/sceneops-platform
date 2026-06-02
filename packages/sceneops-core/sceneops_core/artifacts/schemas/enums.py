from __future__ import annotations

from enum import StrEnum


class ArtifactBackend(StrEnum):
    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"


class ArtifactKind(StrEnum):
    DATASET_MANIFEST = "dataset_manifest"
    SCENE_INDEX = "scene_index"
    SCENE_MANIFEST = "scene_manifest"
    SAMPLE_MANIFEST = "sample_manifest"

    DATASET_VALIDATION_REPORT = "dataset_validation_report"
    DATASET_PROFILE_REPORT = "dataset_profile_report"

    PREDICTION_MANIFEST = "prediction_manifest"
    EVALUATION_MANIFEST = "evaluation_manifest"

    MODEL_ARTIFACT = "model_artifact"
