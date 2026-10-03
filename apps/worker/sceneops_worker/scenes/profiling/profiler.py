from __future__ import annotations

from collections import Counter

from sceneops_core.scenes.schemas import SceneManifest, SceneModality

from .reports import SceneProfileResult


class SceneManifestProfiler:
    def profile(self, *, scene_id: str, manifest: SceneManifest) -> SceneProfileResult:
        modality = {c.channel: c.modality for c in manifest.channels}
        calibrations = {c.calibration_id: c for c in manifest.calibrations}

        total: Counter[str] = Counter()
        calibrated: Counter[str] = Counter()
        with_ego_pose: Counter[str] = Counter()
        with_intrinsic: Counter[str] = Counter()
        with_image_size: Counter[str] = Counter()
        camera_channels: set[str] = set()

        for observation in manifest.observations:
            channel = observation.channel
            total[channel] += 1
            calibration = calibrations.get(observation.calibration_id or "")
            if calibration is not None:
                calibrated[channel] += 1
            if observation.ego_pose_id is not None:
                with_ego_pose[channel] += 1
            if modality[channel] == SceneModality.CAMERA:
                camera_channels.add(channel)
                if calibration is not None and calibration.camera_intrinsic is not None:
                    with_intrinsic[channel] += 1
                if observation.image_size is not None:
                    with_image_size[channel] += 1

        def coverage(counts: Counter[str], channels) -> dict[str, float]:
            return {ch: counts[ch] / total[ch] for ch in sorted(channels)}

        return SceneProfileResult(
            scene_id=scene_id,
            observation_count=len(manifest.observations),
            keyframe_count=len(manifest.keyframes()),
            annotation_count=len(manifest.annotations),
            observed_channels=manifest.observed_channel_names(),
            observations_by_channel=dict(sorted(total.items())),
            category_distribution=dict(
                sorted(Counter(a.category for a in manifest.annotations).items())
            ),
            calibration_coverage=coverage(calibrated, total),
            ego_pose_coverage=coverage(with_ego_pose, total),
            camera_intrinsic_coverage=coverage(with_intrinsic, camera_channels),
            image_size_coverage=coverage(with_image_size, camera_channels),
        )
