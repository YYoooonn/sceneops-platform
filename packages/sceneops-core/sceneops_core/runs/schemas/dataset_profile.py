from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.datasets.schemas import DatasetProfileScope

from .base import BaseRunRecord


class LidarChannelMetrics(SceneOpsBaseModel):
    """Point-cloud statistics for a single LiDAR channel across all profiled frames."""

    channel: str

    frame_count: int | None = None
    total_points: int | None = None
    points_per_frame_mean: float | None = None
    points_per_frame_std: float | None = None
    points_per_frame_min: int | None = None
    points_per_frame_max: int | None = None

    range_mean_m: float | None = None
    range_std_m: float | None = None
    range_min_m: float | None = None
    range_max_m: float | None = None

    height_min_m: float | None = None
    height_max_m: float | None = None

    missing_frame_count: int | None = None
    missing_frame_ratio: float | None = None


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

    lidar_channel_metrics: dict[str, LidarChannelMetrics] | None = None
