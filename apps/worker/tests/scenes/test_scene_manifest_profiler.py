"""Tests for SceneManifestProfiler with registry-based calibration.

Coverage metrics are computed by resolving frame ID references against the
scene-level calibrated_sensors and ego_poses registries.
"""

from __future__ import annotations

import pytest

from sceneops_core.scenes.schemas.manifests import (
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import (
    ImageMetadataManifest,
    SensorCalibrationManifest,
    EgoPoseManifest,
)
from sceneops_worker.scenes.profiling.profiler import SceneManifestProfiler


def _cal(
    cal_id: str = "cs-1", *, with_intrinsic: bool = True
) -> SensorCalibrationManifest:
    return SensorCalibrationManifest(
        calibration_id=cal_id,
        sensor_id="sensor",
        channel="CAM_FRONT",
        translation=[0.0, 0.0, 0.0],
        rotation=[1.0, 0.0, 0.0, 0.0],
        camera_intrinsic=(
            [[800.0, 0.0, 400.0], [0.0, 800.0, 300.0], [0.0, 0.0, 1.0]]
            if with_intrinsic
            else None
        ),
    )


def _lidar_cal(cal_id: str = "cs-lidar") -> SensorCalibrationManifest:
    return SensorCalibrationManifest(
        calibration_id=cal_id,
        sensor_id="lidar",
        channel="LIDAR_TOP",
        translation=[0.0, 0.0, 1.84],
        rotation=[1.0, 0.0, 0.0, 0.0],
    )


def _pose(pose_id: str = "ep-1") -> EgoPoseManifest:
    return EgoPoseManifest(
        ego_pose_id=pose_id,
        translation=[0.0, 0.0, 0.0],
        rotation=[1.0, 0.0, 0.0, 0.0],
    )


def _img() -> ImageMetadataManifest:
    return ImageMetadataManifest(width=1600, height=900)


def _frame(
    frame_id: str,
    channel: str,
    modality: SensorModality,
    *,
    cal_id: str | None = "cs-1",
    pose_id: str | None = "ep-1",
    image: ImageMetadataManifest | None = None,
) -> SceneSensorFrameManifest:
    return SceneSensorFrameManifest(
        frame_id=frame_id,
        sample_id="s-001",
        timestamp_us=0,
        channel=channel,
        modality=modality,
        uri=f"/data/{frame_id}",
        calibrated_sensor_id=cal_id,
        ego_pose_id=pose_id,
        image=image,
    )


def _scene(
    *frames: SceneSensorFrameManifest,
    calibrated_sensors: list[SensorCalibrationManifest] | None = None,
    ego_poses: list[EgoPoseManifest] | None = None,
    extra_samples: list[SceneSampleManifest] | None = None,
) -> SceneManifest:
    all_frames = list(frames)
    if extra_samples:
        sample = SceneSampleManifest(
            sample_id="s-001", scene_id="sc", timestamp_us=0, sensor_frames=all_frames
        )
        samples = [sample] + extra_samples
        all_extra = [f for s in extra_samples for f in s.sensor_frames]
        channels = sorted({f.channel for f in all_frames + all_extra})
        frame_count = len(all_frames) + len(all_extra)
    else:
        sample = SceneSampleManifest(
            sample_id="s-001", scene_id="sc", timestamp_us=0, sensor_frames=all_frames
        )
        samples = [sample]
        channels = sorted({f.channel for f in all_frames})
        frame_count = len(all_frames)

    return SceneManifest(
        scene_id="sc",
        calibrated_sensors=calibrated_sensors or [],
        ego_poses=ego_poses or [],
        samples=samples,
        sample_count=len(samples),
        frame_count=frame_count,
        channels=channels,
    )


_profiler = SceneManifestProfiler()


class TestCalibrationCoverage:
    def test_full_coverage_when_all_frames_resolve(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=_img(),
        )
        lidar = _frame(
            "fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-lidar", pose_id="ep-1"
        )
        result = _profiler.profile(
            manifest=_scene(
                cam,
                lidar,
                calibrated_sensors=[_cal("cs-1"), _lidar_cal("cs-lidar")],
                ego_poses=[_pose("ep-1")],
            )
        )
        assert result.calibration_coverage["CAM_FRONT"] == 1.0
        assert result.calibration_coverage["LIDAR_TOP"] == 1.0

    def test_zero_coverage_when_registry_empty(self) -> None:
        cam = _frame(
            "fc", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-1", image=_img()
        )
        result = _profiler.profile(
            manifest=_scene(cam)
        )  # no calibrated_sensors registry
        assert result.calibration_coverage["CAM_FRONT"] == 0.0

    def test_zero_coverage_when_cal_id_is_none(self) -> None:
        cam = _frame(
            "fc", "CAM_FRONT", SensorModality.CAMERA, cal_id=None, image=_img()
        )
        result = _profiler.profile(
            manifest=_scene(cam, calibrated_sensors=[_cal("cs-1")])
        )
        assert result.calibration_coverage["CAM_FRONT"] == 0.0

    def test_partial_coverage(self) -> None:
        f0 = _frame(
            "f0", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-1", image=_img()
        )
        f1 = _frame(
            "f1", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-missing", image=_img()
        )
        sample = SceneSampleManifest(
            sample_id="s", scene_id="sc", timestamp_us=0, sensor_frames=[f0, f1]
        )
        manifest = SceneManifest(
            scene_id="sc",
            calibrated_sensors=[_cal("cs-1")],
            ego_poses=[],
            samples=[sample],
            sample_count=1,
            frame_count=2,
            channels=["CAM_FRONT"],
        )
        result = _profiler.profile(manifest=manifest)
        assert result.calibration_coverage["CAM_FRONT"] == pytest.approx(0.5)

    def test_coverage_per_channel_independent(self) -> None:
        cam = _frame(
            "fc", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-cam", image=_img()
        )
        lidar = _frame("fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-gone")
        result = _profiler.profile(
            manifest=_scene(
                cam,
                lidar,
                calibrated_sensors=[_cal("cs-cam")],  # only cam calibration in registry
                ego_poses=[_pose()],
            )
        )
        assert result.calibration_coverage["CAM_FRONT"] == 1.0
        assert result.calibration_coverage["LIDAR_TOP"] == 0.0


class TestEgoPoseCoverage:
    def test_full_ego_pose_coverage(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=_img(),
        )
        result = _profiler.profile(
            manifest=_scene(cam, calibrated_sensors=[_cal()], ego_poses=[_pose("ep-1")])
        )
        assert result.ego_pose_coverage["CAM_FRONT"] == 1.0

    def test_zero_ego_pose_coverage_when_registry_empty(self) -> None:
        cam = _frame(
            "fc", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-1", pose_id="ep-1"
        )
        result = _profiler.profile(manifest=_scene(cam, calibrated_sensors=[_cal()]))
        assert result.ego_pose_coverage["CAM_FRONT"] == 0.0

    def test_zero_coverage_when_pose_id_is_none(self) -> None:
        cam = _frame(
            "fc", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-1", pose_id=None
        )
        result = _profiler.profile(
            manifest=_scene(cam, calibrated_sensors=[_cal()], ego_poses=[_pose("ep-1")])
        )
        assert result.ego_pose_coverage["CAM_FRONT"] == 0.0


class TestCameraIntrinsicCoverage:
    def test_full_intrinsic_coverage_camera_only(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=_img(),
        )
        lidar = _frame(
            "fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-lidar", pose_id="ep-1"
        )
        result = _profiler.profile(
            manifest=_scene(
                cam,
                lidar,
                calibrated_sensors=[_cal("cs-1"), _lidar_cal("cs-lidar")],
                ego_poses=[_pose()],
            )
        )
        assert result.camera_intrinsic_coverage["CAM_FRONT"] == 1.0
        assert "LIDAR_TOP" not in result.camera_intrinsic_coverage

    def test_zero_intrinsic_when_cal_has_no_intrinsic(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-no-k",
            pose_id="ep-1",
            image=_img(),
        )
        result = _profiler.profile(
            manifest=_scene(
                cam,
                calibrated_sensors=[_cal("cs-no-k", with_intrinsic=False)],
                ego_poses=[_pose()],
            )
        )
        assert result.camera_intrinsic_coverage["CAM_FRONT"] == 0.0

    def test_zero_intrinsic_when_cal_not_in_registry(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-gone",
            pose_id="ep-1",
            image=_img(),
        )
        result = _profiler.profile(manifest=_scene(cam, ego_poses=[_pose()]))
        assert result.camera_intrinsic_coverage["CAM_FRONT"] == 0.0


class TestImageSizeCoverage:
    def test_full_image_size_coverage(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=_img(),
        )
        result = _profiler.profile(
            manifest=_scene(cam, calibrated_sensors=[_cal()], ego_poses=[_pose()])
        )
        assert result.image_size_coverage["CAM_FRONT"] == 1.0

    def test_zero_image_size_coverage_when_no_image(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=None,
        )
        result = _profiler.profile(
            manifest=_scene(cam, calibrated_sensors=[_cal()], ego_poses=[_pose()])
        )
        assert result.image_size_coverage["CAM_FRONT"] == 0.0

    def test_lidar_channel_excluded_from_image_size_coverage(self) -> None:
        lidar = _frame("fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-lidar")
        result = _profiler.profile(
            manifest=_scene(
                lidar, calibrated_sensors=[_lidar_cal()], ego_poses=[_pose()]
            )
        )
        assert "LIDAR_TOP" not in result.image_size_coverage

    def test_image_with_none_width_not_counted(self) -> None:
        img = ImageMetadataManifest(width=None, height=900)
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=img,
        )
        result = _profiler.profile(
            manifest=_scene(cam, calibrated_sensors=[_cal()], ego_poses=[_pose()])
        )
        assert result.image_size_coverage["CAM_FRONT"] == 0.0


class TestEmptyScene:
    def test_empty_scene_no_coverage_dicts(self) -> None:
        manifest = SceneManifest(
            scene_id="sc", sample_count=0, frame_count=0, channels=[]
        )
        result = _profiler.profile(manifest=manifest)
        assert result.calibration_coverage == {}
        assert result.ego_pose_coverage == {}
        assert result.camera_intrinsic_coverage == {}
        assert result.image_size_coverage == {}
