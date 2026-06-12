from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawSensorFrameManifest,
)
from sceneops_core.scenes.schemas import SampleGroupingConfig
from sceneops_core.sensors.manifests import (
    EgoPoseManifest,
    SensorCalibrationManifest,
)


@dataclass(frozen=True)
class SceneBuildContext:
    manifest: RawLogManifest
    frame_index: RawLogFrameIndex
    sampling: SampleGroupingConfig

    frame_by_id: dict[str, RawSensorFrameManifest]

    calibration_by_id: dict[str, SensorCalibrationManifest]
    calibration_by_sensor_id: dict[str, SensorCalibrationManifest]
    calibration_by_channel: dict[str, SensorCalibrationManifest]

    ego_pose_by_id: dict[str, EgoPoseManifest]
    sorted_ego_poses: list[EgoPoseManifest]

    @classmethod
    def from_frame_index(
        cls,
        *,
        manifest: RawLogManifest,
        frame_index: RawLogFrameIndex,
        sampling: SampleGroupingConfig,
    ) -> "SceneBuildContext":
        calibrations = [
            _to_sensor_calibration(calibration)
            for calibration in frame_index.calibrations
        ]
        ego_poses = [_to_ego_pose(ego_pose) for ego_pose in frame_index.ego_poses]

        return cls(
            manifest=manifest,
            frame_index=frame_index,
            sampling=sampling,
            frame_by_id=_index_frames_by_id(frame_index.frames),
            calibration_by_id=_index_calibrations_by_id(calibrations),
            calibration_by_sensor_id=_index_calibrations_by_sensor_id(calibrations),
            calibration_by_channel=_index_calibrations_by_channel(calibrations),
            ego_pose_by_id=_index_ego_poses_by_id(ego_poses),
            sorted_ego_poses=_sort_ego_poses(ego_poses),
        )

    def frames_for_ids(
        self,
        frame_ids: list[str],
    ) -> list[RawSensorFrameManifest]:
        """Resolve frame ids into timestamp-sorted raw frames.

        Missing frame ids are skipped intentionally. Segment validation can catch
        missing references separately if needed.
        """

        frames = [
            self.frame_by_id[frame_id]
            for frame_id in frame_ids
            if frame_id in self.frame_by_id
        ]
        return sorted(frames, key=lambda frame: frame.timestamp_us)

    def calibration_for_id(
        self,
        calibration_id: str | None,
    ) -> SensorCalibrationManifest | None:
        if calibration_id is None:
            return None
        return self.calibration_by_id.get(calibration_id)

    def ego_pose_for_id(
        self,
        ego_pose_id: str | None,
    ) -> EgoPoseManifest | None:
        if ego_pose_id is None:
            return None
        return self.ego_pose_by_id.get(ego_pose_id)


def _to_sensor_calibration(
    value: object,
) -> SensorCalibrationManifest:
    """Convert raw calibration-like records to scene-level calibration records.

    If RawLogFrameIndex.calibrations already contains SensorCalibrationManifest,
    this is effectively a no-op. If it contains RawCalibrationManifest with the
    same shape, this validates/copies it into the scene-level type.
    """

    if isinstance(value, SensorCalibrationManifest):
        return value

    if hasattr(value, "model_dump"):
        return SensorCalibrationManifest.model_validate(value.model_dump(mode="json"))

    return SensorCalibrationManifest.model_validate(value)


def _to_ego_pose(
    value: object,
) -> EgoPoseManifest:
    """Convert raw ego-pose-like records to scene-level ego-pose records."""

    if isinstance(value, EgoPoseManifest):
        return value

    if hasattr(value, "model_dump"):
        return EgoPoseManifest.model_validate(value.model_dump(mode="json"))

    return EgoPoseManifest.model_validate(value)


def _index_frames_by_id(
    frames: list[RawSensorFrameManifest],
) -> dict[str, RawSensorFrameManifest]:
    return {frame.frame_id: frame for frame in frames}


def _index_calibrations_by_id(
    calibrations: list[SensorCalibrationManifest],
) -> dict[str, SensorCalibrationManifest]:
    return {calibration.calibration_id: calibration for calibration in calibrations}


def _index_calibrations_by_sensor_id(
    calibrations: list[SensorCalibrationManifest],
) -> dict[str, SensorCalibrationManifest]:
    """Index calibration by sensor_id.

    If multiple calibrations share the same sensor_id, keep the first one.
    This is acceptable for the initial static-rig assumption. A later
    time-versioned calibration resolver can replace this policy.
    """

    result: dict[str, SensorCalibrationManifest] = {}

    for calibration in calibrations:
        sensor_id = calibration.sensor_id
        if not sensor_id:
            continue
        result.setdefault(sensor_id, calibration)

    return result


def _index_calibrations_by_channel(
    calibrations: list[SensorCalibrationManifest],
) -> dict[str, SensorCalibrationManifest]:
    """Index calibration by channel as fallback when sensor_id is unavailable."""

    result: dict[str, SensorCalibrationManifest] = {}

    for calibration in calibrations:
        channel = calibration.channel
        if not channel:
            continue
        result.setdefault(channel, calibration)

    return result


def _index_ego_poses_by_id(
    ego_poses: list[EgoPoseManifest],
) -> dict[str, EgoPoseManifest]:
    return {ego_pose.ego_pose_id: ego_pose for ego_pose in ego_poses}


def _sort_ego_poses(
    ego_poses: list[EgoPoseManifest],
) -> list[EgoPoseManifest]:
    """Sort ego poses by timestamp and drop records without timestamp.

    EgoPoseResolver works by nearest timestamp, so timestamp-less records cannot
    participate in resolution.
    """

    timestamped = [
        ego_pose for ego_pose in ego_poses if ego_pose.timestamp_us is not None
    ]

    return sorted(
        timestamped,
        key=lambda ego_pose: ego_pose.timestamp_us or 0,
    )
