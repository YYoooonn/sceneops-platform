"""Batch MCAP sink: simulated receive time, write-once finalization and
fail-loud behavior. Uses hand-built events so nothing nuScenes-specific is
involved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from synthetic_nuscenes import read_mcap

from dataset_acquisition import ros2
from dataset_acquisition.cli import main as cli_main
from dataset_acquisition.events import AcquisitionError, AcquisitionEvent
from dataset_acquisition.mcap_sink import write_mcap


def string_event(
    topic: str, t: int, text: str = "x", sequence: int | None = None
) -> AcquisitionEvent:
    return ros2.event(
        topic,
        ros2.make("std_msgs/msg/String", data=text),
        source_time_ns=t,
        sequence=sequence,
    )


def test_receive_time_is_source_time_and_never_wall_clock(tmp_path: Path) -> None:
    events = [
        string_event("/a", 5, sequence=1),
        string_event("/b", 5),
        string_event("/a", 9, sequence=2),
    ]
    summary = write_mcap(events, tmp_path / "r.mcap")
    messages, metadata, profile = read_mcap(tmp_path / "r.mcap")

    assert profile == "ros2"
    assert [(m.topic, m.log_time, m.publish_time, m.sequence) for m in messages] == [
        ("/a", 5, 5, 1),
        ("/b", 5, 5, 0),
        ("/a", 9, 9, 2),
    ]
    assert [m.data for m in messages] == [e.payload for e in events]  # bytes unchanged
    assert metadata == {}
    assert summary.to_dict()["topic_counts"] == {"/a": 2, "/b": 1}
    assert (summary.first_log_time_ns, summary.last_log_time_ns) == (5, 9)


def test_origin_metadata_record(tmp_path: Path) -> None:
    write_mcap(
        [string_event("/a", 1)], tmp_path / "r.mcap", origin={"b": "2", "a": "1"}
    )
    _, metadata, _ = read_mcap(tmp_path / "r.mcap")
    assert metadata == {"sceneops.acquisition_origin": {"a": "1", "b": "2"}}


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    out = tmp_path / "r.mcap"
    out.write_bytes(b"existing")
    with pytest.raises(AcquisitionError, match="refusing to overwrite"):
        write_mcap([string_event("/a", 1)], out)
    assert out.read_bytes() == b"existing"


def test_failure_leaves_no_recording_and_no_partial(tmp_path: Path) -> None:
    out = tmp_path / "r.mcap"
    with pytest.raises(AcquisitionError, match="out of acquisition order"):
        write_mcap([string_event("/a", 9), string_event("/a", 5)], out)
    assert not out.exists()
    assert not (tmp_path / "r.mcap.partial").exists()


def test_failure_inside_the_adapter_also_cleans_up(tmp_path: Path) -> None:
    def events():
        yield string_event("/a", 1)
        raise OSError("source file vanished")

    with pytest.raises(OSError):
        write_mcap(events(), tmp_path / "r.mcap")
    assert list(tmp_path.iterdir()) == []


def test_foreign_partial_is_reported_not_deleted(tmp_path: Path) -> None:
    partial = tmp_path / "r.mcap.partial"
    partial.write_bytes(b"in progress elsewhere")
    with pytest.raises(AcquisitionError, match="exists"):
        write_mcap([string_event("/a", 1)], tmp_path / "r.mcap")
    assert partial.read_bytes() == b"in progress elsewhere"


def test_empty_stream_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError, match="no events"):
        write_mcap([], tmp_path / "r.mcap")
    assert list(tmp_path.iterdir()) == []


def test_one_topic_one_type(tmp_path: Path) -> None:
    clash = ros2.event(
        "/a",
        ros2.make("builtin_interfaces/msg/Time", sec=1, nanosec=0),
        source_time_ns=2,
    )
    with pytest.raises(AcquisitionError, match="two message types"):
        write_mcap([string_event("/a", 1), clash], tmp_path / "r.mcap")


def test_unset_fields_take_ros2_defaults() -> None:
    quaternion = ros2.make("geometry_msgs/msg/Quaternion")
    assert (quaternion.x, quaternion.y, quaternion.z, quaternion.w) == (
        0.0,
        0.0,
        0.0,
        1.0,
    )
    battery = ros2.make("sensor_msgs/msg/BatteryState", present=True)
    assert battery.present is True and battery.voltage == 0.0
    assert len(battery.cell_voltage) == 0
    with pytest.raises(TypeError, match="no fields"):
        ros2.make("std_msgs/msg/String", text="x")


def test_cli_converts_and_reports(dataroot: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "cli.mcap"
    argv = [
        "nuscenes",
        "--dataroot",
        str(dataroot),
        "--version",
        "v1.0-test",
        "--source-unit",
        "scene-0001",
        "--output",
        str(out),
        "--channels",
        "can,mission",
    ]
    assert cli_main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["path"] == str(out)
    assert summary["topic_counts"]["/mission/status"] == 2
    assert summary["sha256"].startswith("sha256:")

    assert cli_main(argv) == 1  # write-once
    assert "refusing to overwrite" in capsys.readouterr().err
