from __future__ import annotations

from collections import Counter, defaultdict

from sceneops_core.scenes.schemas.manifests import SceneManifest
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import SensorCalibrationManifest

from .reports import SceneProfileResult


class SceneManifestProfiler:
    def profile(self, *, manifest: SceneManifest) -> SceneProfileResult:
        annotation_count = sum(len(s.annotations) for s in manifest.samples)

        category_counts: Counter[str] = Counter()
        for sample in manifest.samples:
            for annotation in sample.annotations:
                category = getattr(annotation, "category", None) or "unknown"
                category_counts[str(category)] += 1

        # Build lookup indexes from scene-level registries
        calibrated_sensor_by_id: dict[str, SensorCalibrationManifest] = {
            c.calibration_id: c for c in manifest.calibrated_sensors
        }
        ego_pose_ids: set[str] = {p.ego_pose_id for p in manifest.ego_poses}

        frame_total: dict[str, int] = defaultdict(int)
        camera_channels: set[str] = set()
        calibration_resolved: dict[str, int] = defaultdict(int)
        ego_pose_resolved: dict[str, int] = defaultdict(int)
        intrinsic_present: dict[str, int] = defaultdict(int)
        image_size_present: dict[str, int] = defaultdict(int)

        for sample in manifest.samples:
            for frame in sample.sensor_frames:
                ch = frame.channel
                frame_total[ch] += 1

                # Count calibration resolution (frame has ID and it resolves)
                if (
                    frame.calibration_id
                    and frame.calibration_id in calibrated_sensor_by_id
                ):
                    calibration_resolved[ch] += 1

                # Count ego_pose resolution
                if frame.ego_pose_id and frame.ego_pose_id in ego_pose_ids:
                    ego_pose_resolved[ch] += 1

                if frame.modality == SensorModality.CAMERA:
                    camera_channels.add(ch)

                    resolved_cal = (
                        calibrated_sensor_by_id.get(frame.calibration_id)
                        if frame.calibration_id
                        else None
                    )
                    if (
                        resolved_cal is not None
                        and resolved_cal.camera_intrinsic is not None
                    ):
                        intrinsic_present[ch] += 1

                    if (
                        frame.image is not None
                        and frame.image.width is not None
                        and frame.image.height is not None
                    ):
                        image_size_present[ch] += 1

        calibration_coverage = {
            ch: calibration_resolved[ch] / total
            for ch, total in frame_total.items()
            if total > 0
        }
        ego_pose_coverage = {
            ch: ego_pose_resolved[ch] / total
            for ch, total in frame_total.items()
            if total > 0
        }
        camera_intrinsic_coverage = {
            ch: intrinsic_present[ch] / frame_total[ch]
            for ch in camera_channels
            if frame_total[ch] > 0
        }
        image_size_coverage = {
            ch: image_size_present[ch] / frame_total[ch]
            for ch in camera_channels
            if frame_total[ch] > 0
        }

        return SceneProfileResult(
            scene_id=manifest.scene_id,
            sample_count=manifest.sample_count,
            frame_count=manifest.frame_count,
            annotation_count=annotation_count,
            channels=list(manifest.channels),
            category_distribution=dict(category_counts),
            calibration_coverage=calibration_coverage,
            ego_pose_coverage=ego_pose_coverage,
            camera_intrinsic_coverage=camera_intrinsic_coverage,
            image_size_coverage=image_size_coverage,
        )
