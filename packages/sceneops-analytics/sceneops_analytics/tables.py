"""Pure Postgres/ArtifactStore record -> columnar table builders.

No I/O here — callers load ``SceneRecord`` rows (Postgres) and verified
``SceneManifest`` revisions (ArtifactStore) themselves and pass them in.
Keeping these functions pure makes them trivially unit-testable and reusable
outside the job/worker runtime (e.g. from a notebook, a future Spark/dbt
stage, or a CLI).

Scene tables are observation-centric like the canonical Scene: every
observation is a row with its own source timestamp and clock, and keyframes
are a separate table over the source-defined grouping. Every timestamp
column is paired with the clock it is counted in; clocks are never
converted. A manifest carries no
scene id, so manifest-based builders take ``(scene_id, manifest)`` pairs.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from sceneops_core.robots.schemas import MissionRecord, RobotStateRecord
from sceneops_core.scenes.schemas import SceneManifest, SceneRecord

SceneManifests = Sequence[tuple[str, SceneManifest]]

SCENES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "robot_run_id": pl.Utf8,
    "unit_key": pl.Utf8,
    "producer_fingerprint": pl.Utf8,
    "manifest_artifact_id": pl.Utf8,
    "manifest_checksum": pl.Utf8,
    "window_clock": pl.Utf8,
    "window_start_timestamp_ns": pl.Int64,
    "window_end_timestamp_ns": pl.Int64,
    "observation_count": pl.Int64,
    "keyframe_count": pl.Int64,
    "observed_channels": pl.List(pl.Utf8),
}

OBSERVATIONS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "observation_id": pl.Utf8,
    "channel": pl.Utf8,
    "modality": pl.Utf8,
    "timestamp_ns": pl.Int64,
    "source_clock": pl.Utf8,
    "payload_artifact_id": pl.Utf8,
    "payload_checksum": pl.Utf8,
    "payload_media_type": pl.Utf8,
    "payload_size_bytes": pl.Int64,
    "calibration_id": pl.Utf8,
    "ego_pose_id": pl.Utf8,
}

KEYFRAMES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "group_id": pl.Utf8,
    "timestamp_ns": pl.Int64,
    "source_clock": pl.Utf8,
    "observation_ids": pl.List(pl.Utf8),
}


def build_scenes_table(scenes: list[SceneRecord]) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": s.dataset_id,
            "dataset_version": s.dataset_version,
            "scene_id": s.scene_id,
            "robot_run_id": s.robot_run_id,
            "unit_key": s.unit_key,
            "producer_fingerprint": s.producer_fingerprint,
            "manifest_artifact_id": s.manifest_artifact_id,
            "manifest_checksum": s.manifest_checksum,
            "window_clock": s.window_clock,
            "window_start_timestamp_ns": s.window_start_timestamp_ns,
            "window_end_timestamp_ns": s.window_end_timestamp_ns,
            "observation_count": s.observation_count,
            "keyframe_count": s.keyframe_count,
            "observed_channels": s.observed_channels,
        }
        for s in scenes
    ]
    return pl.DataFrame(rows, schema=SCENES_SCHEMA)


def build_observations_table(
    *,
    dataset_id: str,
    dataset_version: str,
    manifests: SceneManifests,
) -> pl.DataFrame:
    rows = []
    for scene_id, manifest in manifests:
        modality = {c.channel: c.modality.value for c in manifest.channels}
        clock = {c.channel: c.source_clock for c in manifest.channels}
        rows.extend(
            {
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "scene_id": scene_id,
                "observation_id": o.observation_id,
                "channel": o.channel,
                "modality": modality[o.channel],
                "timestamp_ns": o.timestamp_ns,
                "source_clock": clock[o.channel],
                "payload_artifact_id": o.payload.artifact_id,
                "payload_checksum": o.payload.checksum,
                "payload_media_type": o.payload.media_type,
                "payload_size_bytes": o.payload.size_bytes,
                "calibration_id": o.calibration_id,
                "ego_pose_id": o.ego_pose_id,
            }
            for o in manifest.observations
        )
    return pl.DataFrame(rows, schema=OBSERVATIONS_SCHEMA)


def build_keyframes_table(
    *,
    dataset_id: str,
    dataset_version: str,
    manifests: SceneManifests,
) -> pl.DataFrame:
    rows = []
    for scene_id, manifest in manifests:
        rows.extend(
            {
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "scene_id": scene_id,
                "group_id": g.group_id,
                "timestamp_ns": g.timestamp_ns,
                "source_clock": g.source_clock,
                "observation_ids": list(g.observation_ids),
            }
            for g in manifest.keyframes()
        )
    return pl.DataFrame(rows, schema=KEYFRAMES_SCHEMA)


TABLE_BUILDERS = ("scenes", "observations", "keyframes")


# ── Robot analytics tables (roadmap §7.4: robot_telemetry.parquet, missions.parquet) ──

ROBOT_TELEMETRY_SCHEMA: dict[str, pl.PolarsDataType] = {
    "robot_id": pl.Utf8,
    "robot_run_id": pl.Utf8,
    "mission_id": pl.Utf8,
    "scene_id": pl.Utf8,
    "timestamp_us": pl.Int64,
    "position": pl.List(pl.Float64),
    "orientation": pl.List(pl.Float64),
    "velocity": pl.List(pl.Float64),
    "acceleration": pl.List(pl.Float64),
    "steering": pl.Float64,
    "throttle": pl.Float64,
    "brake": pl.Float64,
    "battery": pl.Float64,
    "operation_state": pl.Utf8,
}

MISSIONS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "mission_id": pl.Utf8,
    "robot_id": pl.Utf8,
    "robot_run_id": pl.Utf8,
    "status": pl.Utf8,
    "started_at": pl.Datetime("us", "UTC"),
    "ended_at": pl.Datetime("us", "UTC"),
}


def build_robot_telemetry_table(states: list[RobotStateRecord]) -> pl.DataFrame:
    rows = [
        {
            "robot_id": s.robot_id,
            "robot_run_id": s.robot_run_id,
            "mission_id": s.mission_id,
            "scene_id": s.scene_id,
            "timestamp_us": s.timestamp_us,
            "position": s.position,
            "orientation": s.orientation,
            "velocity": s.velocity,
            "acceleration": s.acceleration,
            "steering": s.steering,
            "throttle": s.throttle,
            "brake": s.brake,
            "battery": s.battery,
            "operation_state": str(s.operation_state) if s.operation_state else None,
        }
        for s in states
    ]
    return pl.DataFrame(rows, schema=ROBOT_TELEMETRY_SCHEMA)


def build_missions_table(missions: list[MissionRecord]) -> pl.DataFrame:
    rows = [
        {
            "mission_id": m.mission_id,
            "robot_id": m.robot_id,
            "robot_run_id": m.robot_run_id,
            "status": str(m.status),
            "started_at": m.started_at,
            "ended_at": m.ended_at,
        }
        for m in missions
    ]
    return pl.DataFrame(rows, schema=MISSIONS_SCHEMA)


ROBOT_TABLE_BUILDERS = ("robot_telemetry", "missions")
