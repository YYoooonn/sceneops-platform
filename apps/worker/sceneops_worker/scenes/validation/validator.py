from __future__ import annotations

from sceneops_core.scenes.schemas.manifests import SceneManifest
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import SensorCalibrationManifest

from .reports import SceneValidationIssue, SceneValidationResult


class SceneManifestValidator:
    def validate(
        self,
        *,
        manifest: SceneManifest,
        required_channels: list[str] | None = None,
        validate_samples: bool = False,
        block_on_sample_missing_channels: bool = False,
    ) -> SceneValidationResult:
        required = required_channels or []
        observed = manifest.channels
        observed_set = set(observed)

        issues: list[SceneValidationIssue] = []

        if manifest.sample_count == 0:
            issues.append(
                SceneValidationIssue(
                    type="empty_scene",
                    message="Scene has no samples",
                    blocking=True,
                )
            )

        # Scene-level channel check — always blocking
        missing_channels = [ch for ch in required if ch not in observed_set]
        for ch in missing_channels:
            issues.append(
                SceneValidationIssue(
                    type="missing_channel",
                    message=f"Required channel missing: {ch}",
                    channel=ch,
                    blocking=True,
                )
            )

        # Sample-level channel check — blocking only if explicitly requested
        if validate_samples and required:
            for sample in manifest.samples:
                sample_channels = {sf.channel for sf in sample.sensor_frames}
                for ch in required:
                    if ch not in sample_channels:
                        issues.append(
                            SceneValidationIssue(
                                type="sample_missing_channel",
                                message=f"Sample {sample.sample_id} missing channel: {ch}",
                                channel=ch,
                                blocking=block_on_sample_missing_channels,
                            )
                        )

        # Build scene-level lookup indexes for reference resolution
        calibrated_sensor_by_id: dict[str, SensorCalibrationManifest] = {
            c.calibration_id: c for c in manifest.calibrated_sensors
        }
        ego_pose_ids: set[str] = {p.ego_pose_id for p in manifest.ego_poses}

        # Geometry completeness checks — non-blocking warnings
        for sample in manifest.samples:
            for frame in sample.sensor_frames:
                # Check calibrated_sensor reference
                if not frame.calibration_id:
                    issues.append(
                        SceneValidationIssue(
                            type="missing_calibrated_sensor_ref",
                            message=(
                                f"Frame {frame.frame_id} (channel {frame.channel}) "
                                "has no calibration_id"
                            ),
                            channel=frame.channel,
                            blocking=False,
                        )
                    )
                    resolved_cal = None
                else:
                    resolved_cal = calibrated_sensor_by_id.get(frame.calibration_id)
                    if resolved_cal is None:
                        issues.append(
                            SceneValidationIssue(
                                type="missing_calibrated_sensor_record",
                                message=(
                                    f"Frame {frame.frame_id} calibration_id "
                                    f"{frame.calibration_id!r} not found in "
                                    "scene registry"
                                ),
                                channel=frame.channel,
                                blocking=False,
                            )
                        )

                # Check ego_pose reference
                if not frame.ego_pose_id:
                    issues.append(
                        SceneValidationIssue(
                            type="missing_ego_pose_ref",
                            message=(
                                f"Frame {frame.frame_id} (channel {frame.channel}) "
                                "has no ego_pose_id"
                            ),
                            channel=frame.channel,
                            blocking=False,
                        )
                    )
                elif frame.ego_pose_id not in ego_pose_ids:
                    issues.append(
                        SceneValidationIssue(
                            type="missing_ego_pose_record",
                            message=(
                                f"Frame {frame.frame_id} ego_pose_id "
                                f"{frame.ego_pose_id!r} not found in scene registry"
                            ),
                            channel=frame.channel,
                            blocking=False,
                        )
                    )

                if frame.modality == SensorModality.CAMERA:
                    # Camera intrinsic check (requires resolved calibration record)
                    if resolved_cal is None or resolved_cal.camera_intrinsic is None:
                        issues.append(
                            SceneValidationIssue(
                                type="missing_camera_intrinsic",
                                message=(
                                    f"Camera frame {frame.frame_id} "
                                    f"(channel {frame.channel}) missing camera_intrinsic"
                                ),
                                channel=frame.channel,
                                blocking=False,
                            )
                        )

                    # Image size check
                    if frame.image is None or (
                        frame.image.width is None or frame.image.height is None
                    ):
                        issues.append(
                            SceneValidationIssue(
                                type="missing_image_size",
                                message=(
                                    f"Camera frame {frame.frame_id} "
                                    f"(channel {frame.channel}) missing image width/height"
                                ),
                                channel=frame.channel,
                                blocking=False,
                            )
                        )

        should_block = any(i.blocking for i in issues)

        return SceneValidationResult(
            scene_id=manifest.scene_id,
            status="failed" if should_block else "ready",
            should_block=should_block,
            required_channels=required,
            observed_channels=list(observed),
            missing_channels=missing_channels,
            sample_count=manifest.sample_count,
            frame_count=manifest.frame_count,
            issues=issues,
        )
