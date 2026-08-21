"""Pure Postgres/ArtifactStore record -> columnar table builders.

No I/O here — callers load ``SceneRecord`` rows (Postgres) and ``SceneManifest``
documents (ArtifactStore) themselves and pass them in. Keeping these functions
pure makes them trivially unit-testable and reusable outside the job/worker
runtime (e.g. from a notebook, a future Spark/dbt stage, or a CLI).
"""

from __future__ import annotations

import polars as pl

from sceneops_core.scenes.schemas import SceneManifest, SceneRecord

SCENES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "status": pl.Utf8,
    "origin_type": pl.Utf8,
    "generation_method": pl.Utf8,
    "raw_log_id": pl.Utf8,
    "segment_id": pl.Utf8,
    "sample_count": pl.Int64,
    "frame_count": pl.Int64,
    "annotation_count": pl.Int64,
    "channels": pl.List(pl.Utf8),
    "has_ground_truth": pl.Boolean,
    "ground_truth_source": pl.Utf8,
}

SAMPLES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "sample_id": pl.Utf8,
    "timestamp_us": pl.Int64,
    "frame_index": pl.Int64,
    "sensor_frame_count": pl.Int64,
    "annotation_count": pl.Int64,
}

SENSOR_FRAMES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "sample_id": pl.Utf8,
    "frame_id": pl.Utf8,
    "timestamp_us": pl.Int64,
    "channel": pl.Utf8,
    "modality": pl.Utf8,
    "uri": pl.Utf8,
    "calibration_id": pl.Utf8,
    "ego_pose_id": pl.Utf8,
}

ANNOTATIONS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "sample_id": pl.Utf8,
    "annotation_id": pl.Utf8,
    "category": pl.Utf8,
    "instance_id": pl.Utf8,
    "timestamp_us": pl.Int64,
    "coordinate_frame": pl.Utf8,
    "translation": pl.List(pl.Float64),
    "size": pl.List(pl.Float64),
    "rotation": pl.List(pl.Float64),
    "rotation_format": pl.Utf8,
    "attributes": pl.List(pl.Utf8),
    "num_lidar_points": pl.Int64,
    "num_radar_points": pl.Int64,
}


def build_scenes_table(scenes: list[SceneRecord]) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": s.dataset_id,
            "dataset_version": s.dataset_version,
            "scene_id": s.scene_id,
            "status": str(s.status),
            "origin_type": str(s.origin_type),
            "generation_method": str(s.generation_method),
            "raw_log_id": s.raw_log_id,
            "segment_id": s.segment_id,
            "sample_count": s.sample_count,
            "frame_count": s.frame_count,
            "annotation_count": s.annotation_count,
            "channels": s.channels,
            "has_ground_truth": s.has_ground_truth,
            "ground_truth_source": s.ground_truth_source,
        }
        for s in scenes
    ]
    return pl.DataFrame(rows, schema=SCENES_SCHEMA)


def build_samples_table(
    *,
    dataset_id: str,
    dataset_version: str,
    manifests: list[SceneManifest],
) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "scene_id": manifest.scene_id,
            "sample_id": sample.sample_id,
            "timestamp_us": sample.timestamp_us,
            "frame_index": sample.frame_index,
            "sensor_frame_count": len(sample.sensor_frames),
            "annotation_count": len(sample.annotations),
        }
        for manifest in manifests
        for sample in manifest.samples
    ]
    return pl.DataFrame(rows, schema=SAMPLES_SCHEMA)


def build_sensor_frames_table(
    *,
    dataset_id: str,
    dataset_version: str,
    manifests: list[SceneManifest],
) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "scene_id": manifest.scene_id,
            "sample_id": sample.sample_id,
            "frame_id": frame.frame_id,
            "timestamp_us": frame.timestamp_us,
            "channel": frame.channel,
            "modality": str(frame.modality),
            "uri": frame.uri,
            "calibration_id": frame.calibration_id,
            "ego_pose_id": frame.ego_pose_id,
        }
        for manifest in manifests
        for sample in manifest.samples
        for frame in sample.sensor_frames
    ]
    return pl.DataFrame(rows, schema=SENSOR_FRAMES_SCHEMA)


def build_annotations_table(
    *,
    dataset_id: str,
    dataset_version: str,
    manifests: list[SceneManifest],
) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "scene_id": manifest.scene_id,
            "sample_id": sample.sample_id,
            "annotation_id": annotation.annotation_id,
            "category": annotation.category,
            "instance_id": annotation.instance_id,
            "timestamp_us": annotation.timestamp_us,
            "coordinate_frame": annotation.coordinate_frame,
            "translation": annotation.translation,
            "size": annotation.size,
            "rotation": annotation.rotation,
            "rotation_format": annotation.rotation_format,
            "attributes": annotation.attributes,
            "num_lidar_points": annotation.num_lidar_points,
            "num_radar_points": annotation.num_radar_points,
        }
        for manifest in manifests
        for sample in manifest.samples
        for annotation in sample.annotations
    ]
    return pl.DataFrame(rows, schema=ANNOTATIONS_SCHEMA)


TABLE_BUILDERS = ("scenes", "samples", "sensor_frames", "annotations")
