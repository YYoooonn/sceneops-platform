from __future__ import annotations

from bisect import bisect_left

from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.scenes.schemas import EgoPoseResolveStrategy, SampleGroupingConfig
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import (
    EgoPoseManifest,
    ImageMetadataManifest,
    SensorCalibrationManifest,
)

from .context import SceneBuildContext


class CalibrationResolver:
    def resolve(
        self,
        *,
        frame: RawSensorFrameManifest,
        context: SceneBuildContext,
        config: SampleGroupingConfig,
    ) -> SensorCalibrationManifest | None:
        calibration: SensorCalibrationManifest | None = None

        if frame.sensor_id:
            calibration = context.calibration_by_sensor_id.get(frame.sensor_id)

        if calibration is None:
            calibration = context.calibration_by_channel.get(frame.channel)

        if calibration is None and not config.allow_missing_calibration:
            raise ValueError(
                "Failed to resolve calibration for raw frame "
                f"{frame.frame_id!r} channel={frame.channel!r} "
                f"sensor_id={frame.sensor_id!r}"
            )

        return calibration


class EgoPoseResolver:
    def resolve(
        self,
        *,
        timestamp_us: int,
        context: SceneBuildContext,
        config: SampleGroupingConfig,
    ) -> EgoPoseManifest | None:
        if config.ego_pose_strategy == EgoPoseResolveStrategy.NEAREST:
            pose = self._resolve_nearest(
                timestamp_us=timestamp_us,
                context=context,
                tolerance_ms=config.ego_pose_tolerance_ms,
            )
        elif config.ego_pose_strategy == EgoPoseResolveStrategy.EXACT:
            pose = self._resolve_exact(
                timestamp_us=timestamp_us,
                context=context,
            )
        elif config.ego_pose_strategy == EgoPoseResolveStrategy.INTERPOLATE:
            raise NotImplementedError(
                "ego_pose_strategy='interpolate' is reserved for a later phase. "
                "Use 'nearest' or 'exact' for now."
            )
        else:
            raise NotImplementedError(
                f"Unsupported ego pose strategy: {config.ego_pose_strategy!r}"
            )

        if pose is None and not config.allow_missing_ego_pose:
            raise ValueError(
                f"Failed to resolve ego pose at timestamp_us={timestamp_us}"
            )

        return pose

    @staticmethod
    def _resolve_exact(
        *,
        timestamp_us: int,
        context: SceneBuildContext,
    ) -> EgoPoseManifest | None:
        for pose in context.sorted_ego_poses:
            if pose.timestamp_us == timestamp_us:
                return pose
        return None

    @staticmethod
    def _resolve_nearest(
        *,
        timestamp_us: int,
        context: SceneBuildContext,
        tolerance_ms: int,
    ) -> EgoPoseManifest | None:
        poses = context.sorted_ego_poses
        if not poses:
            return None

        timestamps = [
            pose.timestamp_us for pose in poses if pose.timestamp_us is not None
        ]

        idx = bisect_left(timestamps, timestamp_us)

        candidates: list[EgoPoseManifest] = []

        if idx < len(poses):
            candidates.append(poses[idx])
        if idx > 0:
            candidates.append(poses[idx - 1])

        if not candidates:
            return None

        nearest = min(
            candidates,
            key=lambda pose: abs((pose.timestamp_us or 0) - timestamp_us),
        )

        if nearest.timestamp_us is None:
            return None

        delta_us = abs(nearest.timestamp_us - timestamp_us)
        if delta_us > tolerance_ms * 1000:
            return None

        return nearest


class ImageMetadataResolver:
    def resolve(
        self,
        *,
        frame: RawSensorFrameManifest,
    ) -> ImageMetadataManifest | None:
        if frame.modality != SensorModality.CAMERA:
            return None

        width = frame.metadata.get("width")
        height = frame.metadata.get("height")
        fileformat = frame.metadata.get("fileformat")

        if width is None and height is None and fileformat is None:
            return None

        return ImageMetadataManifest(
            width=width,
            height=height,
            fileformat=fileformat,
        )
