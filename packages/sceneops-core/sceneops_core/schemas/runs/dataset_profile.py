from __future__ import annotations

from sceneops_core.schemas.datasets.profile import DatasetProfileScope
from sceneops_core.schemas.runs.base import BaseRunRecord


class DatasetProfileRunRecord(BaseRunRecord):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    profile_report_uri: str | None = None

    scope: DatasetProfileScope = DatasetProfileScope.FULL
    max_samples: int | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    profiled_scene_count: int | None = None
    profiled_sample_count: int | None = None

    observed_channel_count: int | None = None
    missing_required_channel_count: int | None = None
    sensor_coverage_ratio: float | None = None

    empty_annotation_sample_count: int | None = None
    empty_annotation_sample_ratio: float | None = None
