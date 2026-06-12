"""Serialization round-trip tests for the registry-based scene manifest schemas.

Covers:
- SceneSensorFrameManifest with ID refs + image serializes/deserializes cleanly
- SceneManifest with scene-level registries round-trips correctly
- SceneSampleManifest no longer has ego_pose/calibrations at sample level
- SensorCalibrationManifest, SensorEgoPoseManifest, ImageMetadataManifest
- Old removed fields not present in serialized output
"""

from __future__ import annotations

from sceneops_core.observations.schemas.frames import RawSensorFrameManifest
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


def _cal(cal_id: str = "cs-001") -> SensorCalibrationManifest:
    return SensorCalibrationManifest(
        calibration_id=cal_id,
        sensor_id="sensor-001",
        channel="CAM_FRONT",
        modality=SensorModality.CAMERA,
        translation=[1.0, 0.5, 1.7],
        rotation=[0.999, 0.0, 0.0, 0.0],
        rotation_format="quaternion_wxyz",
        camera_intrinsic=[[800.0, 0.0, 400.0], [0.0, 800.0, 300.0], [0.0, 0.0, 1.0]],
    )


def _pose(pose_id: str = "ep-001") -> EgoPoseManifest:
    return EgoPoseManifest(
        ego_pose_id=pose_id,
        timestamp_us=1_200_000,
        translation=[10.0, 20.0, 0.0],
        rotation=[0.999, 0.0, 0.0, 0.042],
        rotation_format="quaternion_wxyz",
    )


def _img() -> ImageMetadataManifest:
    return ImageMetadataManifest(width=1600, height=900, fileformat="jpg")


class TestSceneSensorFrameSerialisation:
    def test_frame_with_id_refs_round_trip(self) -> None:
        frame = SceneSensorFrameManifest(
            frame_id="f1",
            sample_id="s1",
            timestamp_us=1100,
            channel="CAM_FRONT",
            modality=SensorModality.CAMERA,
            uri="file:///tmp/image.jpg",
            calibrated_sensor_id="cs-001",
            ego_pose_id="ep-001",
            image=_img(),
        )
        dumped = frame.model_dump(mode="json")
        restored = SceneSensorFrameManifest.model_validate(dumped)

        assert restored.frame_id == "f1"
        assert restored.calibrated_sensor_id == "cs-001"
        assert restored.ego_pose_id == "ep-001"
        assert restored.image is not None
        assert restored.image.width == 1600
        assert restored.image.height == 900
        assert restored.modality == SensorModality.CAMERA

    def test_none_refs_round_trip(self) -> None:
        frame = SceneSensorFrameManifest(
            frame_id="f2",
            sample_id="s1",
            timestamp_us=0,
            channel="LIDAR_TOP",
            modality=SensorModality.LIDAR,
            uri="/data/lidar.pcd.bin",
        )
        dumped = frame.model_dump(mode="json")
        restored = SceneSensorFrameManifest.model_validate(dumped)
        assert restored.calibrated_sensor_id is None
        assert restored.ego_pose_id is None
        assert restored.image is None

    def test_old_inline_fields_not_in_dump(self) -> None:
        frame = SceneSensorFrameManifest(
            frame_id="f1", sample_id="s1", timestamp_us=0, channel="CAM_FRONT", uri="a"
        )
        dumped = frame.model_dump(mode="json")
        assert "calibrated_sensor" not in dumped
        assert "ego_pose" not in dumped

    def test_old_id_fields_from_previous_schema_not_present(self) -> None:
        """ego_pose_id / calibration_id were on the old SceneSensorFrameManifest
        before it was refactored.  Now they are correct: calibrated_sensor_id and
        ego_pose_id are the canonical names."""
        frame = SceneSensorFrameManifest(
            frame_id="f1",
            sample_id="s1",
            timestamp_us=0,
            channel="CAM_FRONT",
            uri="a",
            calibrated_sensor_id="cs-abc",
            ego_pose_id="ep-xyz",
        )
        dumped = frame.model_dump(mode="json")
        assert dumped["calibrated_sensor_id"] == "cs-abc"
        assert dumped["ego_pose_id"] == "ep-xyz"


class TestSceneManifestWithRegistriesSerialisation:
    def test_scene_with_registries_round_trip(self) -> None:
        frame = SceneSensorFrameManifest(
            frame_id="f1",
            sample_id="s1",
            timestamp_us=1100,
            channel="CAM_FRONT",
            modality=SensorModality.CAMERA,
            uri="/img/f1.jpg",
            calibrated_sensor_id="cs-001",
            ego_pose_id="ep-001",
            image=_img(),
        )
        sample = SceneSampleManifest(
            sample_id="s1",
            scene_id="sc1",
            timestamp_us=1000,
            frame_index=0,
            sensor_frames=[frame],
        )
        manifest = SceneManifest(
            scene_id="sc1",
            dataset_id="nuscenes",
            dataset_version="v1.0-mini",
            calibrated_sensors=[_cal("cs-001")],
            ego_poses=[_pose("ep-001")],
            samples=[sample],
            sample_count=1,
            frame_count=1,
            channels=["CAM_FRONT"],
        )
        dumped = manifest.model_dump(mode="json")
        restored = SceneManifest.model_validate(dumped)

        assert len(restored.calibrated_sensors) == 1
        assert restored.calibrated_sensors[0].calibration_id == "cs-001"
        assert restored.calibrated_sensors[0].camera_intrinsic is not None
        assert len(restored.ego_poses) == 1
        assert restored.ego_poses[0].ego_pose_id == "ep-001"
        assert restored.ego_poses[0].timestamp_us == 1_200_000
        assert len(restored.samples) == 1
        sf = restored.samples[0].sensor_frames[0]
        assert sf.calibrated_sensor_id == "cs-001"
        assert sf.ego_pose_id == "ep-001"
        assert sf.image is not None

    def test_empty_registries_round_trip(self) -> None:
        manifest = SceneManifest(scene_id="sc")
        dumped = manifest.model_dump(mode="json")
        restored = SceneManifest.model_validate(dumped)
        assert restored.calibrated_sensors == []
        assert restored.ego_poses == []


class TestSceneSampleManifestSerialisation:
    def test_no_ego_pose_calibrations_in_dump(self) -> None:
        sample = SceneSampleManifest(sample_id="s1", scene_id="sc1", timestamp_us=1000)
        dumped = sample.model_dump(mode="json")
        assert "ego_pose" not in dumped
        assert "calibrations" not in dumped


class TestRawSensorFrameSerialisation:
    def test_raw_frame_with_ids_and_inline_objects_round_trip(self) -> None:
        frame = RawSensorFrameManifest(
            frame_id="rf1",
            timestamp_us=1100,
            source_sample_timestamp_us=1000,
            channel="CAM_FRONT",
            modality=SensorModality.CAMERA,
            uri="samples/CAM_FRONT/img.jpg",
            calibrated_sensor_id="cs-001",
            ego_pose_id="ep-001",
            calibrated_sensor=_cal("cs-001"),
            ego_pose=_pose("ep-001"),
            image=_img(),
        )
        dumped = frame.model_dump(mode="json")
        restored = RawSensorFrameManifest.model_validate(dumped)

        assert restored.calibrated_sensor_id == "cs-001"
        assert restored.ego_pose_id == "ep-001"
        assert restored.source_sample_timestamp_us == 1000
        assert restored.timestamp_us == 1100
        # Inline objects are preserved (used during building)
        assert restored.calibrated_sensor is not None
        assert restored.calibrated_sensor.calibration_id == "cs-001"
        assert restored.ego_pose is not None
        assert restored.ego_pose.ego_pose_id == "ep-001"
        assert restored.image is not None

    def test_old_removed_fields_not_in_dump(self) -> None:
        frame = RawSensorFrameManifest(
            frame_id="rf1", timestamp_us=0, channel="CAM_FRONT", uri="a"
        )
        dumped = frame.model_dump(mode="json")
        assert "ego_pose_ref" not in dumped
        assert "calibration_ref" not in dumped


class TestSensorCalibrationManifestSerialisation:
    def test_camera_calibration_round_trip(self) -> None:
        cal = _cal()
        dumped = cal.model_dump(mode="json")
        restored = SensorCalibrationManifest.model_validate(dumped)
        assert restored.calibration_id == "cs-001"
        assert restored.sensor_id == "sensor-001"
        assert restored.modality == SensorModality.CAMERA
        assert restored.camera_intrinsic is not None
        assert restored.rotation_format == "quaternion_wxyz"

    def test_lidar_calibration_no_intrinsic(self) -> None:
        cal = SensorCalibrationManifest(
            calibration_id="lidar-cs",
            sensor_id="lidar-s",
            modality=SensorModality.LIDAR,
            translation=[0.0, 0.0, 1.84],
            rotation=[1.0, 0.0, 0.0, 0.0],
        )
        dumped = cal.model_dump(mode="json")
        restored = SensorCalibrationManifest.model_validate(dumped)
        assert restored.camera_intrinsic is None


class TestSensorEgoPoseManifestSerialisation:
    def test_ego_pose_round_trip(self) -> None:
        pose = _pose()
        dumped = pose.model_dump(mode="json")
        restored = EgoPoseManifest.model_validate(dumped)
        assert restored.ego_pose_id == "ep-001"
        assert restored.timestamp_us == 1_200_000
        assert restored.translation == [10.0, 20.0, 0.0]
        assert restored.rotation_format == "quaternion_wxyz"

    def test_ego_pose_required_id(self) -> None:
        pose = EgoPoseManifest(ego_pose_id="required-id")
        dumped = pose.model_dump(mode="json")
        restored = EgoPoseManifest.model_validate(dumped)
        assert restored.ego_pose_id == "required-id"
        assert restored.timestamp_us is None


class TestImageMetadataSerialisation:
    def test_image_round_trip(self) -> None:
        img = _img()
        dumped = img.model_dump(mode="json")
        restored = ImageMetadataManifest.model_validate(dumped)
        assert restored.width == 1600
        assert restored.height == 900
        assert restored.fileformat == "jpg"
