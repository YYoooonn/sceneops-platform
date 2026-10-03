"""Real nuScenes v1.0-mini: convert one full scene and check the recording
message by message against the source tables, files and CAN bus extract.

Skipped when the dataset is absent. ``NUSCENES_DATAROOT`` overrides the
repository's ``data/raw/nuscenes``.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from synthetic_nuscenes import read_mcap, stamp_ns

from dataset_acquisition.mcap_sink import write_mcap
from dataset_acquisition.nuscenes import (
    NuScenesAdapter,
    NuScenesSelection,
    camera_image_topic,
    camera_info_topic,
    lidar_topic,
    sensor_frame,
)

DATAROOT = Path(
    os.environ.get(
        "NUSCENES_DATAROOT", Path(__file__).resolve().parents[3] / "data/raw/nuscenes"
    )
)
VERSION = "v1.0-mini"
UNIT = "scene-0061"

pytestmark = pytest.mark.skipif(
    not (DATAROOT / VERSION / "scene.json").is_file(),
    reason=f"nuScenes {VERSION} not at {DATAROOT}",
)


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    from nuscenes.nuscenes import NuScenes

    nusc = NuScenes(version=VERSION, dataroot=str(DATAROOT), verbose=False)
    adapter = NuScenesAdapter(
        NuScenesSelection(dataroot=DATAROOT, version=VERSION, source_unit=UNIT),
        nusc=nusc,
    )
    out = tmp_path_factory.mktemp("mini") / f"{UNIT}.mcap"
    summary = write_mcap(adapter.events(), out, origin=adapter.origin())
    messages, metadata, profile = read_mcap(out)

    scene = next(s for s in nusc.scene if s["name"] == UNIT)
    samples, token = set(), scene["first_sample_token"]
    while token:
        samples.add(token)
        token = nusc.get("sample", token)["next"]
    unit_data = sorted(
        (sd for sd in nusc.sample_data if sd["sample_token"] in samples),
        key=lambda sd: (sd["timestamp"], sd["token"]),
    )
    yield nusc, unit_data, summary, messages, metadata, profile
    out.unlink()


def _topic(messages, topic):
    return [m for m in messages if m.topic == topic]


def _can(name):
    return json.loads((DATAROOT / "can_bus" / f"{UNIT}_{name}.json").read_text())


def test_cameras_are_source_frames_with_intrinsics(converted) -> None:
    nusc, unit_data, _, messages, _, _ = converted
    channels = sorted(
        {sd["channel"] for sd in unit_data if sd["sensor_modality"] == "camera"}
    )
    assert len(channels) == 6
    for channel in channels:
        source = [sd for sd in unit_data if sd["channel"] == channel]
        images = _topic(messages, camera_image_topic(channel))
        infos = _topic(messages, camera_info_topic(channel))
        assert len(images) == len(infos) == len(source)
        intrinsic = nusc.get("calibrated_sensor", source[0]["calibrated_sensor_token"])[
            "camera_intrinsic"
        ]
        for image, info, sd in zip(images, infos, source, strict=True):
            assert stamp_ns(image.decoded.header.stamp) == sd["timestamp"] * 1000
            assert image.decoded.header.frame_id == sensor_frame(channel)
            assert bytes(image.decoded.data) == (DATAROOT / sd["filename"]).read_bytes()
            assert stamp_ns(info.decoded.header.stamp) == sd["timestamp"] * 1000
            assert list(info.decoded.k) == [v for row in intrinsic for v in row]
            assert (info.decoded.width, info.decoded.height) == (
                sd["width"],
                sd["height"],
            )


def test_lidar_sweeps_are_source_point_clouds(converted) -> None:
    _, unit_data, _, messages, _, _ = converted
    source = [sd for sd in unit_data if sd["channel"] == "LIDAR_TOP"]
    clouds = _topic(messages, lidar_topic("LIDAR_TOP"))
    assert len(clouds) == len(source) > 300  # key frames and sweeps
    for cloud, sd in zip(clouds, source, strict=True):
        raw = (DATAROOT / sd["filename"]).read_bytes()
        assert stamp_ns(cloud.decoded.header.stamp) == sd["timestamp"] * 1000
        assert bytes(cloud.decoded.data) == raw
        assert cloud.decoded.width * cloud.decoded.point_step == len(raw)


def test_static_calibration_and_ego_pose(converted) -> None:
    nusc, unit_data, _, messages, _, _ = converted
    (static,) = _topic(messages, "/tf_static")
    transforms = {t.child_frame_id: t for t in static.decoded.transforms}
    converted_channels = {
        sd["channel"]
        for sd in unit_data
        if sd["sensor_modality"] in ("camera", "lidar")
    }
    assert set(transforms) == {sensor_frame(c) for c in converted_channels}
    for channel in converted_channels:
        sd = next(s for s in unit_data if s["channel"] == channel)
        calibration = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
        t = transforms[sensor_frame(channel)]
        w, x, y, z = calibration["rotation"]
        assert t.header.frame_id == "base_link"
        tr, rot = t.transform.translation, t.transform.rotation
        assert [tr.x, tr.y, tr.z] == calibration["translation"]
        assert [rot.x, rot.y, rot.z, rot.w] == [x, y, z, w]

    poses = _topic(messages, "/tf")
    assert len(poses) == len(unit_data)
    for pose, sd in zip(poses, unit_data, strict=True):
        ego = nusc.get("ego_pose", sd["ego_pose_token"])
        (t,) = pose.decoded.transforms
        assert stamp_ns(t.header.stamp) == ego["timestamp"] * 1000
        assert [t.transform.translation.x, t.transform.translation.y] == ego[
            "translation"
        ][:2]


def test_can_bus_messages(converted) -> None:
    _, _, _, messages, _, _ = converted
    for topic, name in (("/vehicle/odom", "pose"), ("/vehicle/imu", "ms_imu")):
        assert [stamp_ns(m.decoded.header.stamp) for m in _topic(messages, topic)] == [
            r["utime"] * 1000 for r in sorted(_can(name), key=lambda r: r["utime"])
        ]
    monitor = sorted(_can("vehicle_monitor"), key=lambda r: r["utime"])
    control = _topic(messages, "/vehicle/control")
    assert [json.loads(m.decoded.data)["source_timestamp_ns"] for m in control] == [
        r["utime"] * 1000 for r in monitor
    ]
    assert len(_topic(messages, "/vehicle/status")) == len(monitor)


def test_timing_ordering_and_extent(converted) -> None:
    _, unit_data, summary, messages, metadata, profile = converted
    assert profile == "ros2"
    source_ns = [sd["timestamp"] * 1000 for sd in unit_data] + [
        r["utime"] * 1000
        for n in ("pose", "ms_imu", "vehicle_monitor")
        for r in _can(n)
    ]
    first, last = min(source_ns), max(source_ns)
    assert (summary.first_log_time_ns, summary.last_log_time_ns) == (first, last)

    log_times = [m.log_time for m in messages]
    assert log_times == sorted(log_times)
    assert all(m.publish_time == m.log_time for m in messages)
    for m in messages:
        header = getattr(m.decoded, "header", None)
        if header is not None:
            assert stamp_ns(header.stamp) == m.log_time
    running, completed = _topic(messages, "/mission/status")
    assert json.loads(running.decoded.data)["source_timestamp_ns"] == first
    assert json.loads(completed.decoded.data)["source_timestamp_ns"] == last

    sequences: dict[str, list[int]] = {}
    for m in messages:
        sequences.setdefault(m.topic, []).append(m.sequence)
    assert all(s == list(range(1, len(s) + 1)) for s in sequences.values())
    assert Counter(m.topic for m in messages) == summary.topic_counts
    assert metadata["sceneops.acquisition_origin"]["source_unit"] == UNIT
