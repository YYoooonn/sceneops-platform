"""Deterministic canonical SceneManifest builders for tests.

Test-support code only: production code must never import this module.
It exists so core, DB, worker and API test suites build canonical Scenes
from one definition instead of each hand-rolling manifest JSON.

The default Scene has two channels (a camera and a lidar), one keyframe
group per entry in ``keyframe_timestamps_ns`` holding one observation of
each channel, one extra camera observation between consecutive keyframes
(a non-keyframe "sweep" that stays canonical) and one ego pose per
observation. Every timestamp uses ``source_clock`` unless ``camera_clock``
gives the camera channel its own clock. Payloads reference the artifact ids
``payload_artifact_id(observation_id, payload_namespace)`` with the bytes
``payload_bytes(observation_id)``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from sceneops_core.artifacts.schemas.payload import PayloadRef
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.provenance import ProducerInfo, RecordingSegmentSource

from .schemas import (
    FrameTransform,
    ImageSize,
    SceneCalibration,
    SceneChannel,
    SceneCoordinateFrame,
    SceneLineage,
    SceneManifest,
    SceneObservation,
    SceneObservationGroup,
    ScenePose,
)

DEFAULT_PRODUCER_ID = "sceneops.test_scene_producer"
IDENTITY_ROTATION = (1.0, 0.0, 0.0, 0.0)
_CAMERA_INTRINSIC = ((1266.4, 0.0, 816.3), (0.0, 1266.4, 491.5), (0.0, 0.0, 1.0))


def recording_source(
    *,
    robot_run_id: str = "run-001",
    unit_key: str = "segment-0000",
    start_timestamp_ns: int = 0,
    end_timestamp_ns: int = 10_000_000_000,
    recording_checksum: str = "sha256:" + "1" * 64,
    source_clock: str = "mcap_log_time",
) -> RecordingSegmentSource:
    return RecordingSegmentSource(
        robot_run_id=robot_run_id,
        recording_artifact_id=robot_run_recording_artifact_id(robot_run_id),
        recording_checksum=recording_checksum,
        source_clock=source_clock,
        start_timestamp_ns=start_timestamp_ns,
        end_timestamp_ns=end_timestamp_ns,
        unit_key=unit_key,
    )


def payload_bytes(observation_id: str) -> bytes:
    """The deterministic bytes behind a test observation's payload."""
    return f"payload:{observation_id}".encode()


def payload_artifact_id(observation_id: str, namespace: str = "art-payload") -> str:
    return f"{namespace}-{observation_id}"


def payload_ref(
    observation_id: str, *, media_type: str, namespace: str = "art-payload"
) -> PayloadRef:
    data = payload_bytes(observation_id)
    return PayloadRef(
        artifact_id=payload_artifact_id(observation_id, namespace),
        checksum="sha256:" + hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )


def build_scene_manifest(
    *,
    source: RecordingSegmentSource | None = None,
    source_clock: str | None = None,
    camera_clock: str | None = None,
    payload_namespace: str = "art-payload",
    producer_id: str = DEFAULT_PRODUCER_ID,
    semantics_version: int = 1,
    build_config: Mapping[str, Any] | None = None,
    camera_channel: str = "CAM_FRONT",
    lidar_channel: str = "LIDAR_TOP",
    keyframe_timestamps_ns: Sequence[int] = (1_000_000_000, 1_500_000_000),
) -> SceneManifest:
    source = source if source is not None else recording_source()
    if source_clock is None:
        source_clock = source.source_clock
    camera_clock = camera_clock or source_clock
    config = (
        dict(build_config)
        if build_config is not None
        else {"channels": sorted([camera_channel, lidar_channel])}
    )
    producer = ProducerInfo.create(
        producer_id=producer_id,
        semantics_version=semantics_version,
        build_config=config,
        source=source.source_revision(),
    )

    frames = sorted(
        [
            SceneCoordinateFrame(frame_id="ego", role="ego"),
            SceneCoordinateFrame(frame_id="world", role="world"),
            SceneCoordinateFrame(frame_id=camera_channel, role="sensor"),
            SceneCoordinateFrame(frame_id=lidar_channel, role="sensor"),
        ],
        key=lambda f: f.frame_id,
    )
    channels = sorted(
        [
            SceneChannel(
                channel=camera_channel,
                modality="camera",
                frame_id=camera_channel,
                source_clock=camera_clock,
            ),
            SceneChannel(
                channel=lidar_channel,
                modality="lidar",
                frame_id=lidar_channel,
                source_clock=source_clock,
            ),
        ],
        key=lambda c: c.channel,
    )
    calibrations = sorted(
        [
            SceneCalibration(
                calibration_id="cal-camera",
                channel=camera_channel,
                extrinsic=FrameTransform(
                    parent_frame_id="ego",
                    child_frame_id=camera_channel,
                    translation_m=(1.7, 0.0, 1.5),
                    rotation_wxyz=(0.5, -0.5, 0.5, -0.5),
                ),
                camera_intrinsic=_CAMERA_INTRINSIC,
            ),
            SceneCalibration(
                calibration_id="cal-lidar",
                channel=lidar_channel,
                extrinsic=FrameTransform(
                    parent_frame_id="ego",
                    child_frame_id=lidar_channel,
                    translation_m=(0.9, 0.0, 1.8),
                    rotation_wxyz=IDENTITY_ROTATION,
                ),
            ),
        ],
        key=lambda c: c.calibration_id,
    )

    observations: list[SceneObservation] = []
    poses: list[ScenePose] = []
    groups: list[SceneObservationGroup] = []

    def observe(channel: str, timestamp_ns: int, *, camera: bool) -> SceneObservation:
        observation_id = f"{'cam' if camera else 'lidar'}-{timestamp_ns}"
        pose_id = f"pose-{observation_id}"
        poses.append(
            ScenePose(
                pose_id=pose_id,
                timestamp_ns=timestamp_ns,
                source_clock=camera_clock if camera else source_clock,
                transform=FrameTransform(
                    parent_frame_id="world",
                    child_frame_id="ego",
                    translation_m=(timestamp_ns / 1e9, 0.0, 0.0),
                    rotation_wxyz=IDENTITY_ROTATION,
                ),
            )
        )
        observation = SceneObservation(
            observation_id=observation_id,
            channel=channel,
            timestamp_ns=timestamp_ns,
            payload=payload_ref(
                observation_id,
                namespace=payload_namespace,
                media_type="image/jpeg" if camera else "application/x.test.pointcloud",
            ),
            calibration_id="cal-camera" if camera else "cal-lidar",
            ego_pose_id=pose_id,
            image_size=ImageSize(width_px=1600, height_px=900) if camera else None,
        )
        observations.append(observation)
        return observation

    ordered = sorted(keyframe_timestamps_ns)
    for index, timestamp_ns in enumerate(ordered):
        camera = observe(camera_channel, timestamp_ns + 7, camera=True)
        lidar = observe(lidar_channel, timestamp_ns, camera=False)
        group_id = f"keyframe-{index:04d}"
        groups.append(
            SceneObservationGroup(
                group_id=group_id,
                kind="keyframe",
                timestamp_ns=timestamp_ns,
                source_clock=source_clock,
                observation_ids=sorted([camera.observation_id, lidar.observation_id]),
            )
        )
        if index + 1 < len(ordered):
            sweep_ns = (timestamp_ns + ordered[index + 1]) // 2
            observe(camera_channel, sweep_ns, camera=True)

    return SceneManifest(
        lineage=SceneLineage(source=source, producer=producer),
        coordinate_frames=frames,
        channels=channels,
        calibrations=calibrations,
        observations=sorted(
            observations, key=lambda o: (o.channel, o.timestamp_ns, o.observation_id)
        ),
        poses=sorted(poses, key=lambda p: (p.source_clock, p.timestamp_ns, p.pose_id)),
        groups=groups,
    )


def semantic_scene_content(manifest: SceneManifest) -> dict[str, Any]:
    """The canonical semantic content of a Scene (ADR-007 §30.9): the
    manifest with every acquisition-provenance-dependent field removed.

    Removed: the source block's RobotRun, recording artifact and recording
    checksum, the producer fingerprint (it covers the source revision), and
    each payload's artifact id. Kept: build configuration, segment window
    and unit key, channels, frames, calibrations, observations (ids,
    timestamps, payload checksum / size / media type), poses and groups.
    Two semantically equivalent recordings built with the same producer and
    a source-semantic configuration have equal content."""
    data = manifest.model_dump(mode="json")
    source = data["lineage"]["source"]
    for field in ("robot_run_id", "recording_artifact_id", "recording_checksum"):
        source.pop(field)
    data["lineage"]["producer"].pop("producer_fingerprint")
    for observation in data["observations"]:
        observation["payload"].pop("artifact_id")
    return data


__all__ = [
    "DEFAULT_PRODUCER_ID",
    "build_scene_manifest",
    "semantic_scene_content",
    "payload_artifact_id",
    "payload_bytes",
    "payload_ref",
    "recording_source",
]
