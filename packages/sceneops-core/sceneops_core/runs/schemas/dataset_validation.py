from __future__ import annotations

from sceneops_core.datasets.schemas import (
    DatasetValidationScope,
    DatasetValidationStatus,
)

from .base import BaseRunRecord


class DatasetValidationRunRecord(BaseRunRecord):
    dataset_id: str
    dataset_version: str

    validation_status: DatasetValidationStatus | None = None
    should_block_pipeline: bool = False

    dataset_manifest_uri: str | None = None
    validation_report_uri: str | None = None

    scope: DatasetValidationScope = DatasetValidationScope.FULL
    max_samples: int | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    validated_scene_count: int | None = None
    validated_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    missing_scene_count: int | None = None
    missing_sample_count: int | None = None
    missing_channel_count: int | None = None
    missing_artifact_count: int | None = None
