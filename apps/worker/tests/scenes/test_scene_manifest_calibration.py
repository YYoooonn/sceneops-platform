"""Tests for registry-based calibration / ego_pose pass-through.

Covers:
- SceneSensorFrameManifest stores calibrated_sensor_id / ego_pose_id (not inline objects).
- SceneManifest has scene-level calibrated_sensors[] and ego_poses[] registries.
- SceneSampleManifest has no sample-level ego_pose/calibrations.
- SampleGrouper passes calibrated_sensor_id / ego_pose_id through to scene frames.
- source_sample_timestamp_us is used for sample.timestamp_us in FRAME_ID strategy.
"""

from __future__ import annotations


from sceneops_core.observations.schemas.frames import RawSensorFrameManifest
from sceneops_core.scenes.schemas.manifests import (
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
from sceneops_core.scenes.schemas.sampling import (
    SampleGroupingConfig,
    SampleGroupingStrategy,
    SensorSyncPolicy,
)
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import (
    ImageMetadataManifest,
    SensorCalibrationManifest,
    EgoPoseManifest,
)
from sceneops_worker.scenes.sample_grouping import SampleGrouper


def _make_cal(
    channel: str = "CAM_FRONT", cal_id: str = "cs-001"
) -> SensorCalibrationManifest:
    return SensorCalibrationManifest(
        calibration_id=cal_id,
        sensor_id="sensor-001",
        channel=channel,
        modality=SensorModality.CAMERA,
        translation=[1.0, 2.0, 3.0],
        rotation=[1.0, 0.0, 0.0, 0.0],
        camera_intrinsic=[[800.0, 0.0, 400.0], [0.0, 800.0, 300.0], [0.0, 0.0, 1.0]],
    )


def _make_pose(pose_id: str = "ep-001") -> EgoPoseManifest:
    return EgoPoseManifest(
        ego_pose_id=pose_id,
        timestamp_us=1_200_000,  # from ego_pose["timestamp"], distinct from sample/frame ts
        translation=[10.0, 20.0, 0.0],
        rotation=[1.0, 0.0, 0.0, 0.0],
    )


def _make_image() -> ImageMetadataManifest:
    return ImageMetadataManifest(width=1600, height=900, fileformat="jpg")


def _raw_frame(
    frame_id: str,
    timestamp_us: int,
    channel: str = "CAM_FRONT",
    *,
    source_frame_id: str | None = None,
    source_sample_timestamp_us: int | None = None,
    cal_id: str = "cs-001",
    pose_id: str = "ep-001",
    with_geometry: bool = True,
) -> RawSensorFrameManifest:
    modality = (
        SensorModality.CAMERA if channel.startswith("CAM") else SensorModality.LIDAR
    )
    return RawSensorFrameManifest(
        frame_id=frame_id,
        timestamp_us=timestamp_us,
        channel=channel,
        modality=modality,
        uri=f"/data/{frame_id}.jpg",
        source_frame_id=source_frame_id,
        source_sample_timestamp_us=source_sample_timestamp_us,
        calibrated_sensor_id=cal_id if with_geometry else None,
        ego_pose_id=pose_id if with_geometry else None,
        calibrated_sensor=_make_cal(channel, cal_id) if with_geometry else None,
        ego_pose=_make_pose(pose_id) if with_geometry else None,
        image=_make_image()
        if (with_geometry and modality == SensorModality.CAMERA)
        else None,
    )


# ── schema: SceneSensorFrameManifest uses ID refs, not inline objects ─────────


class TestSceneSensorFrameManifestSchema:
    def test_has_calibrated_sensor_id_field(self) -> None:
        f = SceneSensorFrameManifest(
            frame_id="f1", sample_id="s1", timestamp_us=0, channel="CAM_FRONT", uri="a"
        )
        assert f.calibrated_sensor_id is None

    def test_has_ego_pose_id_field(self) -> None:
        f = SceneSensorFrameManifest(
            frame_id="f1", sample_id="s1", timestamp_us=0, channel="CAM_FRONT", uri="a"
        )
        assert f.ego_pose_id is None

    def test_has_image_field(self) -> None:
        f = SceneSensorFrameManifest(
            frame_id="f1", sample_id="s1", timestamp_us=0, channel="CAM_FRONT", uri="a"
        )
        assert f.image is None

    def test_no_inline_calibrated_sensor_field(self) -> None:
        assert "calibrated_sensor" not in SceneSensorFrameManifest.model_fields

    def test_no_inline_ego_pose_field(self) -> None:
        assert "ego_pose" not in SceneSensorFrameManifest.model_fields

    def test_stores_calibrated_sensor_id(self) -> None:
        f = SceneSensorFrameManifest(
            frame_id="f1",
            sample_id="s1",
            timestamp_us=0,
            channel="CAM_FRONT",
            uri="a",
            calibrated_sensor_id="cs-token-abc",
        )
        assert f.calibrated_sensor_id == "cs-token-abc"

    def test_stores_ego_pose_id(self) -> None:
        f = SceneSensorFrameManifest(
            frame_id="f1",
            sample_id="s1",
            timestamp_us=0,
            channel="CAM_FRONT",
            uri="a",
            ego_pose_id="ep-token-xyz",
        )
        assert f.ego_pose_id == "ep-token-xyz"

    def test_stores_image(self) -> None:
        img = _make_image()
        f = SceneSensorFrameManifest(
            frame_id="f1",
            sample_id="s1",
            timestamp_us=0,
            channel="CAM_FRONT",
            uri="a",
            image=img,
        )
        assert f.image is not None
        assert f.image.width == 1600


class TestSceneManifestRegistries:
    def test_has_calibrated_sensors_registry(self) -> None:
        m = SceneManifest(scene_id="sc")
        assert isinstance(m.calibrated_sensors, list)

    def test_has_ego_poses_registry(self) -> None:
        m = SceneManifest(scene_id="sc")
        assert isinstance(m.ego_poses, list)

    def test_stores_calibrated_sensors(self) -> None:
        cal = _make_cal()
        m = SceneManifest(scene_id="sc", calibrated_sensors=[cal])
        assert len(m.calibrated_sensors) == 1
        assert m.calibrated_sensors[0].calibration_id == "cs-001"

    def test_stores_ego_poses(self) -> None:
        pose = _make_pose()
        m = SceneManifest(scene_id="sc", ego_poses=[pose])
        assert len(m.ego_poses) == 1
        assert m.ego_poses[0].ego_pose_id == "ep-001"


class TestSceneSampleManifestSchema:
    def test_no_ego_pose_field_at_sample_level(self) -> None:
        assert "ego_pose" not in SceneSampleManifest.model_fields

    def test_no_calibrations_field_at_sample_level(self) -> None:
        assert "calibrations" not in SceneSampleManifest.model_fields


# ── SampleGrouper: passes IDs through ─────────────────────────────────────────


class TestFrameIdPassThrough:
    def _grouper(self) -> SampleGrouper:
        return SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )

    def test_calibrated_sensor_id_passed_through(self) -> None:
        frames = [_raw_frame("f0", 1100, source_frame_id="g0", cal_id="cs-abc")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert sf.calibrated_sensor_id == "cs-abc"

    def test_ego_pose_id_passed_through(self) -> None:
        frames = [_raw_frame("f0", 1100, source_frame_id="g0", pose_id="ep-xyz")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert sf.ego_pose_id == "ep-xyz"

    def test_image_passed_through(self) -> None:
        frames = [_raw_frame("f0", 1100, source_frame_id="g0")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert sf.image is not None
        assert sf.image.width == 1600

    def test_no_inline_calibrated_sensor_on_scene_frame(self) -> None:
        frames = [_raw_frame("f0", 1100, source_frame_id="g0")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert "calibrated_sensor" not in type(sf).model_fields

    def test_none_ids_passed_through(self) -> None:
        frames = [_raw_frame("f0", 1100, source_frame_id="g0", with_geometry=False)]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert sf.calibrated_sensor_id is None
        assert sf.ego_pose_id is None
        assert sf.image is None


class TestTimeBucketPassThrough:
    def _grouper(self) -> SampleGrouper:
        return SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.TIME_BUCKET,
                sample_time_window_ms=500.0,
            )
        )

    def test_calibrated_sensor_id_passed_through(self) -> None:
        frames = [_raw_frame("f0", 0, cal_id="cs-tb")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        assert samples[0].sensor_frames[0].calibrated_sensor_id == "cs-tb"

    def test_ego_pose_id_passed_through(self) -> None:
        frames = [_raw_frame("f0", 0, pose_id="ep-tb")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        assert samples[0].sensor_frames[0].ego_pose_id == "ep-tb"

    def test_image_passed_through(self) -> None:
        frames = [_raw_frame("f0", 0)]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        assert samples[0].sensor_frames[0].image is not None


class TestNearestTimestampPassThrough:
    def _grouper(self) -> SampleGrouper:
        return SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.NEAREST_TIMESTAMP,
                reference_channel="CAM_FRONT",
                sync_policy=SensorSyncPolicy.BEST_EFFORT,
            )
        )

    def test_calibrated_sensor_id_passed_through(self) -> None:
        frames = [_raw_frame("f0", 0, "CAM_FRONT", cal_id="cs-nt")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        assert samples[0].sensor_frames[0].calibrated_sensor_id == "cs-nt"

    def test_ego_pose_id_passed_through(self) -> None:
        frames = [_raw_frame("f0", 0, "CAM_FRONT", pose_id="ep-nt")]
        samples, _ = self._grouper().group(frames, scene_id="sc")
        assert samples[0].sensor_frames[0].ego_pose_id == "ep-nt"


# ── Timestamp semantics ───────────────────────────────────────────────────────


class TestTimestampSemantics:
    """Verify the three distinct timestamps are never collapsed."""

    def test_sample_timestamp_uses_source_sample_timestamp(self) -> None:
        # sample["timestamp"] = 1000, sample_data["timestamp"] = 1100
        frame = _raw_frame(
            "f0",
            timestamp_us=1100,  # sample_data["timestamp"]
            source_frame_id="grp",
            source_sample_timestamp_us=1000,  # sample["timestamp"]
        )
        grouper = SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )
        samples, _ = grouper.group([frame], scene_id="sc")
        assert samples[0].timestamp_us == 1000
        assert samples[0].sensor_frames[0].timestamp_us == 1100

    def test_frame_timestamp_is_sample_data_timestamp(self) -> None:
        frame = _raw_frame(
            "f0",
            timestamp_us=1100,
            source_frame_id="grp",
            source_sample_timestamp_us=1000,
        )
        grouper = SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )
        samples, _ = grouper.group([frame], scene_id="sc")
        assert samples[0].sensor_frames[0].timestamp_us == 1100

    def test_ego_pose_timestamp_is_independent(self) -> None:
        # ego_pose["timestamp"] = 1200 (set on EgoPoseManifest stored in raw frame)
        pose = EgoPoseManifest(
            ego_pose_id="ep1",
            timestamp_us=1200,
            translation=[0.0, 0.0, 0.0],
            rotation=[1.0, 0.0, 0.0, 0.0],
        )
        frame = RawSensorFrameManifest(
            frame_id="f0",
            timestamp_us=1100,
            source_sample_timestamp_us=1000,
            channel="CAM_FRONT",
            modality=SensorModality.CAMERA,
            uri="/img/f0.jpg",
            source_frame_id="grp",
            calibrated_sensor_id="cs1",
            ego_pose_id="ep1",
            ego_pose=pose,
        )
        grouper = SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )
        samples, _ = grouper.group([frame], scene_id="sc")
        sample = samples[0]
        assert sample.timestamp_us == 1000  # sample["timestamp"]
        assert sample.sensor_frames[0].timestamp_us == 1100  # sample_data["timestamp"]
        # ego_pose timestamp is on the pose record itself, not on sample or frame
        assert pose.timestamp_us == 1200

    def test_timestamps_all_different(self) -> None:
        pose = EgoPoseManifest(
            ego_pose_id="ep1",
            timestamp_us=1200,
            translation=[0.0, 0.0, 0.0],
            rotation=[1.0, 0.0, 0.0, 0.0],
        )
        frame = RawSensorFrameManifest(
            frame_id="f0",
            timestamp_us=1100,
            source_sample_timestamp_us=1000,
            channel="CAM_FRONT",
            modality=SensorModality.CAMERA,
            uri="/img.jpg",
            source_frame_id="grp",
            ego_pose_id="ep1",
            ego_pose=pose,
        )
        grouper = SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )
        samples, _ = grouper.group([frame], scene_id="sc")
        s_ts = samples[0].timestamp_us
        f_ts = samples[0].sensor_frames[0].timestamp_us
        p_ts = pose.timestamp_us
        assert s_ts == 1000
        assert f_ts == 1100
        assert p_ts == 1200
        assert len({s_ts, f_ts, p_ts}) == 3  # all three must be different

    def test_calibration_has_no_timestamp(self) -> None:
        cal = _make_cal()
        assert (
            not hasattr(cal, "timestamp_us")
            or "timestamp_us" not in type(cal).model_fields
        )

    def test_sample_fallback_to_min_frame_ts_when_no_source_sample_ts(self) -> None:
        f0 = RawSensorFrameManifest(
            frame_id="f0",
            timestamp_us=500,
            channel="CAM_FRONT",
            modality=SensorModality.CAMERA,
            uri="/a",
            source_frame_id="grp",
        )
        f1 = RawSensorFrameManifest(
            frame_id="f1",
            timestamp_us=600,
            channel="LIDAR_TOP",
            modality=SensorModality.LIDAR,
            uri="/b",
            source_frame_id="grp",
        )
        grouper = SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )
        samples, _ = grouper.group([f0, f1], scene_id="sc")
        assert samples[0].timestamp_us == 500  # min frame timestamp


# ── Calibration de-duplication in scene building ─────────────────────────────


class TestCalibrationDeduplication:
    """Verify that registries deduplicate by ID when built from raw frames."""

    def test_same_calibration_id_deduped(self) -> None:
        # Two frames with the same calibrated_sensor_id (same camera across samples)
        f0 = _raw_frame(
            "f0", 1100, source_frame_id="s0", cal_id="cs-shared", pose_id="ep-0"
        )
        f1 = _raw_frame(
            "f1", 2100, source_frame_id="s1", cal_id="cs-shared", pose_id="ep-1"
        )
        # Build registry manually (as RawSceneBuilder would)
        cal_registry = {}
        for f in [f0, f1]:
            if f.calibrated_sensor is not None and f.calibrated_sensor_id:
                cal_registry[f.calibrated_sensor_id] = f.calibrated_sensor
        assert len(cal_registry) == 1
        assert "cs-shared" in cal_registry

    def test_different_calibration_ids_not_deduped(self) -> None:
        f0 = _raw_frame("f0", 1100, "CAM_FRONT", source_frame_id="s0", cal_id="cs-cam")
        f1 = _raw_frame(
            "f1", 1100, "LIDAR_TOP", source_frame_id="s0", cal_id="cs-lidar"
        )
        cal_registry = {}
        for f in [f0, f1]:
            if f.calibrated_sensor is not None and f.calibrated_sensor_id:
                cal_registry[f.calibrated_sensor_id] = f.calibrated_sensor
        assert len(cal_registry) == 2

    def test_frame_ids_reference_registry(self) -> None:
        frames = [_raw_frame("f0", 1100, source_frame_id="g0", cal_id="cs-abc")]
        grouper = SampleGrouper(
            SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID)
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert sf.calibrated_sensor_id == "cs-abc"
        # Build registry from raw frames
        cal_registry = {frames[0].calibrated_sensor_id: frames[0].calibrated_sensor}
        assert sf.calibrated_sensor_id in cal_registry
