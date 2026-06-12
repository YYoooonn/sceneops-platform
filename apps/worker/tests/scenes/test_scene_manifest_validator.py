"""Tests for SceneManifestValidator with registry-based calibration.

Covers:
- Existing channel checks remain blocking.
- Non-blocking warnings for missing calibrated_sensor_id / ego_pose_id.
- Non-blocking warnings for ID not found in scene registry.
- Non-blocking warnings for camera frames: missing camera_intrinsic, missing image size.
- should_block is False for geometry-only issues.
- Modality-based (not channel-name-based) camera detection.
"""

from __future__ import annotations

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
from sceneops_worker.scenes.validation.validator import SceneManifestValidator


def _cal(
    cal_id: str = "cs-1",
    channel: str = "CAM_FRONT",
    *,
    with_intrinsic: bool = True,
) -> SensorCalibrationManifest:
    return SensorCalibrationManifest(
        calibration_id=cal_id,
        sensor_id="sensor-1",
        channel=channel,
        modality=SensorModality.CAMERA,
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
        sensor_id="lidar-sensor",
        channel="LIDAR_TOP",
        modality=SensorModality.LIDAR,
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
    frames: list[SceneSensorFrameManifest],
    calibrated_sensors: list[SensorCalibrationManifest] | None = None,
    ego_poses: list[EgoPoseManifest] | None = None,
    channels: list[str] | None = None,
) -> SceneManifest:
    sample = SceneSampleManifest(
        sample_id="s-001", scene_id="sc-001", timestamp_us=0, sensor_frames=frames
    )
    ch = channels or sorted({f.channel for f in frames})
    return SceneManifest(
        scene_id="sc-001",
        calibrated_sensors=calibrated_sensors or [],
        ego_poses=ego_poses or [],
        samples=[sample],
        sample_count=1,
        frame_count=len(frames),
        channels=ch,
    )


_validator = SceneManifestValidator()


class TestExistingBlockingBehaviorUnchanged:
    def test_empty_scene_is_blocking(self) -> None:
        manifest = SceneManifest(
            scene_id="sc", sample_count=0, frame_count=0, channels=[]
        )
        result = _validator.validate(manifest=manifest)
        assert result.should_block is True
        assert any(i.type == "empty_scene" and i.blocking for i in result.issues)

    def test_missing_required_channel_is_blocking(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=_img(),
        )
        manifest = _scene(
            [cam],
            calibrated_sensors=[_cal()],
            ego_poses=[_pose()],
        )
        result = _validator.validate(
            manifest=manifest, required_channels=["CAM_FRONT", "LIDAR_TOP"]
        )
        assert result.should_block is True
        blocking = [i for i in result.issues if i.blocking]
        assert any(
            i.type == "missing_channel" and i.channel == "LIDAR_TOP" for i in blocking
        )

    def test_fully_valid_scene_no_issues(self) -> None:
        cam = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-cam",
            pose_id="ep-1",
            image=_img(),
        )
        lidar = _frame(
            "fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-lidar", pose_id="ep-1"
        )
        manifest = _scene(
            [cam, lidar],
            calibrated_sensors=[_cal("cs-cam"), _lidar_cal("cs-lidar")],
            ego_poses=[_pose("ep-1")],
        )
        result = _validator.validate(
            manifest=manifest, required_channels=["CAM_FRONT", "LIDAR_TOP"]
        )
        assert result.should_block is False
        assert len(result.issues) == 0


class TestMissingCalibratedSensorRefWarning:
    def test_missing_calibrated_sensor_id_emits_ref_warning(self) -> None:
        frame = _frame(
            "f0", "CAM_FRONT", SensorModality.CAMERA, cal_id=None, image=_img()
        )
        manifest = _scene([frame], ego_poses=[_pose()])
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_calibrated_sensor_ref" in types

    def test_missing_calibrated_sensor_ref_is_non_blocking(self) -> None:
        frame = _frame(
            "f0", "CAM_FRONT", SensorModality.CAMERA, cal_id=None, image=_img()
        )
        manifest = _scene([frame], ego_poses=[_pose()])
        result = _validator.validate(manifest=manifest)
        assert result.should_block is False

    def test_calibrated_sensor_id_not_in_registry_emits_record_warning(self) -> None:
        # Frame has a cal_id but no matching record in the registry
        frame = _frame(
            "f0",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-missing",
            pose_id="ep-1",
            image=_img(),
        )
        manifest = _scene(
            [frame], ego_poses=[_pose("ep-1")]
        )  # empty calibrated_sensors
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_calibrated_sensor_record" in types

    def test_missing_record_is_non_blocking(self) -> None:
        frame = _frame(
            "f0", "CAM_FRONT", SensorModality.CAMERA, cal_id="cs-missing", image=_img()
        )
        manifest = _scene([frame])
        result = _validator.validate(manifest=manifest)
        assert result.should_block is False


class TestMissingEgoPoseWarning:
    def test_missing_ego_pose_id_emits_ref_warning(self) -> None:
        frame = _frame(
            "f0",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id=None,
            image=_img(),
        )
        manifest = _scene([frame], calibrated_sensors=[_cal()])
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_ego_pose_ref" in types

    def test_missing_ego_pose_id_is_non_blocking(self) -> None:
        frame = _frame(
            "f0",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id=None,
            image=_img(),
        )
        manifest = _scene([frame], calibrated_sensors=[_cal()])
        result = _validator.validate(manifest=manifest)
        assert result.should_block is False

    def test_ego_pose_id_not_in_registry_emits_record_warning(self) -> None:
        frame = _frame(
            "f0",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-missing",
            image=_img(),
        )
        manifest = _scene([frame], calibrated_sensors=[_cal()])  # empty ego_poses
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_ego_pose_record" in types


class TestCameraIntrinsicWarning:
    def test_missing_intrinsic_in_registry_emits_warning(self) -> None:
        cal_no_k = _cal("cs-no-k", with_intrinsic=False)
        frame = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-no-k",
            pose_id="ep-1",
            image=_img(),
        )
        manifest = _scene([frame], calibrated_sensors=[cal_no_k], ego_poses=[_pose()])
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_camera_intrinsic" in types

    def test_missing_intrinsic_is_non_blocking(self) -> None:
        cal_no_k = _cal("cs-no-k", with_intrinsic=False)
        frame = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-no-k",
            pose_id="ep-1",
            image=_img(),
        )
        manifest = _scene([frame], calibrated_sensors=[cal_no_k], ego_poses=[_pose()])
        result = _validator.validate(manifest=manifest)
        assert result.should_block is False

    def test_lidar_frame_no_intrinsic_warning(self) -> None:
        frame = _frame(
            "fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-lidar", pose_id="ep-1"
        )
        manifest = _scene(
            [frame], calibrated_sensors=[_lidar_cal()], ego_poses=[_pose()]
        )
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_camera_intrinsic" not in types

    def test_camera_with_no_cal_record_also_triggers_intrinsic_warning(self) -> None:
        # Frame has cal_id but record not in registry → both record-missing AND intrinsic
        frame = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-gone",
            pose_id="ep-1",
            image=_img(),
        )
        manifest = _scene([frame], ego_poses=[_pose()])
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_calibrated_sensor_record" in types
        assert "missing_camera_intrinsic" in types


class TestImageSizeWarning:
    def test_missing_image_field_emits_warning(self) -> None:
        frame = _frame(
            "fc",
            "CAM_FRONT",
            SensorModality.CAMERA,
            cal_id="cs-1",
            pose_id="ep-1",
            image=None,
        )
        manifest = _scene([frame], calibrated_sensors=[_cal()], ego_poses=[_pose()])
        result = _validator.validate(manifest=manifest)
        assert any(i.type == "missing_image_size" for i in result.issues)

    def test_missing_image_size_is_non_blocking(self) -> None:
        frame = _frame("fc", "CAM_FRONT", SensorModality.CAMERA, image=None)
        manifest = _scene([frame])
        result = _validator.validate(manifest=manifest)
        assert result.should_block is False

    def test_lidar_frame_no_image_size_warning(self) -> None:
        frame = _frame(
            "fl", "LIDAR_TOP", SensorModality.LIDAR, cal_id="cs-lidar", pose_id="ep-1"
        )
        manifest = _scene(
            [frame], calibrated_sensors=[_lidar_cal()], ego_poses=[_pose()]
        )
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_image_size" not in types


class TestModalityBasedDetection:
    def test_camera_modality_with_non_cam_channel_triggers_camera_checks(self) -> None:
        frame = SceneSensorFrameManifest(
            frame_id="fx",
            sample_id="s",
            timestamp_us=0,
            channel="SENSOR_RGB",  # non-standard channel name
            modality=SensorModality.CAMERA,  # but modality is CAMERA
            uri="/data/fx",
            calibrated_sensor_id="cs-1",
            ego_pose_id="ep-1",
            image=None,  # triggers missing_image_size
        )
        manifest = _scene(
            [frame],
            calibrated_sensors=[_cal("cs-1", "SENSOR_RGB")],
            ego_poses=[_pose()],
        )
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_image_size" in types

    def test_lidar_modality_with_cam_channel_no_camera_warnings(self) -> None:
        frame = SceneSensorFrameManifest(
            frame_id="fx",
            sample_id="s",
            timestamp_us=0,
            channel="CAM_WEIRD_LIDAR",  # starts with CAM
            modality=SensorModality.LIDAR,  # but modality is LIDAR
            uri="/data/fx",
            calibrated_sensor_id="cs-lidar",
            ego_pose_id="ep-1",
        )
        manifest = _scene(
            [frame], calibrated_sensors=[_lidar_cal("cs-lidar")], ego_poses=[_pose()]
        )
        result = _validator.validate(manifest=manifest)
        types = [i.type for i in result.issues]
        assert "missing_camera_intrinsic" not in types
        assert "missing_image_size" not in types
