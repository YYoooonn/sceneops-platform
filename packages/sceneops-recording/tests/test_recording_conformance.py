"""L1 raw-recording conformance suite (ADR-007 §29.5): each check is driven
by a real MCAP (ROS 2 profile, CDR payloads serialized from embedded
schemas) that conforms except for the one defect under test.

The suite is writer-independent; the external acquisition integration's real
nuScenes output is checked by tools/e2e/e2e_batch_acquisition.sh.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sceneops_recording import check_l1_recording
from sceneops_publisher.cli import main as cli_main

_TIME = "================================================================================\nMSG: builtin_interfaces/Time\nint32 sec\nuint32 nanosec\n"
_HEADER = (
    "================================================================================\n"
    "MSG: std_msgs/Header\nbuiltin_interfaces/Time stamp\nstring frame_id\n" + _TIME
)
_DEFS = {
    "sensor_msgs/msg/CompressedImage": "std_msgs/Header header\nstring format\nuint8[] data\n"
    + _HEADER,
    "sensor_msgs/msg/CameraInfo": "std_msgs/Header header\nuint32 height\nuint32 width\n"
    + _HEADER,
    "sensor_msgs/msg/PointCloud2": "std_msgs/Header header\nuint8[] data\n" + _HEADER,
    "tf2_msgs/msg/TFMessage": (
        "geometry_msgs/TransformStamped[] transforms\n"
        "================================================================================\n"
        "MSG: geometry_msgs/TransformStamped\nstd_msgs/Header header\n"
        "string child_frame_id\n" + _HEADER
    ),
    "std_msgs/msg/String": "string data\n",
    "sceneops_msgs/msg/SceneMarker": "string data\n",
}

T0 = 1_532_402_927_000_000_000


def _header(t: int, frame: str) -> dict:
    return {
        "stamp": {"sec": t // 1_000_000_000, "nanosec": t % 1_000_000_000},
        "frame_id": frame,
    }


def tf_static(t: int, *children: str) -> dict:
    return {
        "transforms": [
            {"header": _header(t, "base_link"), "child_frame_id": c} for c in children
        ]
    }


def image(t: int, frame: str = "cam_front") -> dict:
    return {"header": _header(t, frame), "format": "jpeg", "data": b"\xff\xd8jpeg"}


def camera_info(t: int, frame: str = "cam_front") -> dict:
    return {"header": _header(t, frame), "height": 900, "width": 1600}


def cloud(t: int, frame: str = "lidar_top") -> dict:
    return {"header": _header(t, frame), "data": b"\x00" * 20}


@dataclass
class Msg:
    topic: str
    schema: str
    payload: dict | bytes
    log_time: int
    sequence: int = 0
    publish_time: int | None = None


@dataclass
class Recording:
    messages: list[Msg] = field(default_factory=list)
    metadata: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    message_encoding: str = "cdr"
    profile: str = "ros2"
    # Register a second channel definition for this topic.
    duplicate_topic: str | None = None

    def write(
        self, path: Path, *, chunk_size: int = 1024 * 1024, compression=None
    ) -> Path:
        from mcap.writer import CompressionType, Writer
        from mcap_ros2._dynamic import serialize_dynamic

        with path.open("wb") as stream:
            writer = Writer(
                stream,
                chunk_size=chunk_size,
                compression=compression or CompressionType.ZSTD,
            )
            writer.start(profile=self.profile, library="test")
            schema_ids: dict[str, int] = {}
            channel_ids: dict[str, int] = {}
            for msg in self.messages:
                if msg.schema not in schema_ids:
                    schema_ids[msg.schema] = writer.register_schema(
                        name=msg.schema,
                        encoding="ros2msg",
                        data=_DEFS[msg.schema].encode(),
                    )
                if msg.topic not in channel_ids:
                    channel_ids[msg.topic] = writer.register_channel(
                        topic=msg.topic,
                        message_encoding=self.message_encoding,
                        schema_id=schema_ids[msg.schema],
                    )
                    if msg.topic == self.duplicate_topic:
                        writer.register_channel(
                            topic=msg.topic,
                            message_encoding=self.message_encoding,
                            schema_id=schema_ids[msg.schema],
                        )
                data = (
                    msg.payload
                    if isinstance(msg.payload, bytes)
                    else serialize_dynamic(msg.schema, _DEFS[msg.schema])[msg.schema](
                        msg.payload
                    )
                )
                writer.add_message(
                    channel_id=channel_ids[msg.topic],
                    log_time=msg.log_time,
                    publish_time=msg.publish_time or msg.log_time,
                    data=data,
                    sequence=msg.sequence,
                )
            for name, values in self.metadata:
                writer.add_metadata(name, values)
            for name in self.attachments:
                writer.add_attachment(
                    create_time=0,
                    log_time=0,
                    name=name,
                    media_type="text/plain",
                    data=b"x",
                )
            writer.finish()
        return path


def conforming() -> Recording:
    """Static transforms first, then per-frame CameraInfo + image, lidar
    and telemetry, each with a per-channel sequence."""
    rec = Recording(
        messages=[
            Msg(
                "/tf_static",
                "tf2_msgs/msg/TFMessage",
                tf_static(T0, "cam_front", "lidar_top"),
                T0,
                1,
            )
        ]
    )
    for i in range(3):
        t = T0 + i * 50_000_000
        rec.messages += [
            Msg(
                "/camera/front/camera_info",
                "sensor_msgs/msg/CameraInfo",
                camera_info(t),
                t,
                i + 1,
            ),
            Msg(
                "/camera/front/image/compressed",
                "sensor_msgs/msg/CompressedImage",
                image(t),
                t,
                i + 1,
            ),
            Msg(
                "/lidar/top/points",
                "sensor_msgs/msg/PointCloud2",
                cloud(t + 1),
                t + 1,
                i + 1,
            ),
            Msg(
                "/vehicle/control", "std_msgs/msg/String", {"data": "{}"}, t + 2, i + 1
            ),
        ]
    rec.metadata.append(
        ("sceneops.acquisition_origin", {"tool": "test", "source_format": "anything"})
    )
    return rec


def violations(report) -> set[tuple[str, str]]:
    return {(v.requirement, v.check) for v in report.violations}


def test_conforming_recording_reports_channel_facts(tmp_path: Path) -> None:
    report = check_l1_recording(conforming().write(tmp_path / "ok.mcap"))

    assert report.conforms, report.violations
    assert report.profile == "ros2"
    assert report.message_count == 13
    image_channel = report.channels["/camera/front/image/compressed"]
    assert image_channel.schema_name == "sensor_msgs/msg/CompressedImage"
    assert (image_channel.message_encoding, image_channel.schema_encoding) == (
        "cdr",
        "ros2msg",
    )
    assert image_channel.message_count == 3
    assert image_channel.frame_ids == ("cam_front",)
    assert (image_channel.first_stamp_ns, image_channel.last_stamp_ns) == (
        T0,
        T0 + 100_000_000,
    )
    assert image_channel.sequenced
    # TFMessage stamps come from its transforms; String carries none.
    assert report.channels["/tf_static"].first_stamp_ns == T0
    assert report.channels["/vehicle/control"].first_stamp_ns is None
    assert report.acquisition_origin == {"tool": "test", "source_format": "anything"}


def test_zero_stamped_static_transforms_conform(tmp_path: Path) -> None:
    """Static transforms often carry an unstamped (zero) header. R9 asks
    only that a transform connecting each sensor frame is recorded at or
    before the first observation, by position; a zero stamp is the
    source's own value and is not a violation."""
    rec = conforming()
    rec.messages[0].payload = tf_static(0, "cam_front", "lidar_top")

    report = check_l1_recording(rec.write(tmp_path / "zero-static.mcap"))

    assert report.conforms, report.violations
    assert report.channels["/tf_static"].first_stamp_ns == 0


def test_stream_digest_ignores_chunking_and_compression(tmp_path: Path) -> None:
    from mcap.writer import CompressionType

    a = check_l1_recording(conforming().write(tmp_path / "a.mcap"))
    b = check_l1_recording(
        conforming().write(
            tmp_path / "b.mcap", chunk_size=64, compression=CompressionType.NONE
        )
    )
    assert (tmp_path / "a.mcap").read_bytes() != (tmp_path / "b.mcap").read_bytes()
    assert a.message_stream_sha256 == b.message_stream_sha256

    rec = conforming()
    rec.messages[1].log_time += 1
    c = check_l1_recording(rec.write(tmp_path / "c.mcap"))
    assert c.message_stream_sha256 != a.message_stream_sha256


def test_topic_with_two_channel_definitions(tmp_path: Path) -> None:
    rec = conforming()
    rec.duplicate_topic = "/vehicle/control"
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert ("R1", "single_channel_definition") in violations(report)


def test_payload_that_does_not_decode_with_its_schema(tmp_path: Path) -> None:
    rec = conforming()
    rec.messages.append(
        Msg(
            "/lidar/top/points",
            "sensor_msgs/msg/PointCloud2",
            b"\x00\x01",
            T0 + 10**9,
            9,
        )
    )
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert ("R2", "payload_decodes") in violations(report)


def test_log_time_must_follow_write_order(tmp_path: Path) -> None:
    rec = conforming()
    rec.messages.append(
        Msg("/vehicle/control", "std_msgs/msg/String", {"data": "{}"}, T0 - 1, 9)
    )
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert violations(report) == {("R4", "receive_order")}


def test_channel_sequence_must_increase(tmp_path: Path) -> None:
    rec = conforming()
    rec.messages[-1].sequence = 1  # /vehicle/control: 1, 2, 1
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert violations(report) == {("R6", "channel_sequence")}


def test_unsequenced_channels_are_allowed(tmp_path: Path) -> None:
    rec = conforming()
    for msg in rec.messages:
        msg.sequence = 0
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert report.conforms
    assert not report.channels["/vehicle/control"].sequenced


def test_sensor_frame_without_static_transform(tmp_path: Path) -> None:
    rec = conforming()
    rec.messages[0] = Msg(
        "/tf_static", "tf2_msgs/msg/TFMessage", tf_static(T0, "cam_front"), T0, 1
    )
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert violations(report) == {("R9", "sensor_transform")}
    assert "lidar_top" in report.violations[0].detail


def test_static_transform_recorded_after_first_observation(tmp_path: Path) -> None:
    rec = conforming()
    static = rec.messages.pop(0)
    static.log_time = T0 + 200_000_000
    rec.messages.append(static)
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert ("R9", "sensor_transform") in violations(report)


def test_image_without_camera_info(tmp_path: Path) -> None:
    rec = conforming()
    rec.messages = [m for m in rec.messages if not m.topic.endswith("camera_info")]
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert violations(report) == {("R9", "camera_info")}


def test_canonical_semantics_are_rejected(tmp_path: Path) -> None:
    rec = conforming()
    rec.metadata.append(("robot_notes", {"scene_id": "s-1", "operator": "x"}))
    rec.metadata.append(("sceneops.scene_boundaries", {"start": "0"}))
    rec.attachments.append("sceneops.dataset_version")
    rec.messages.append(
        Msg("/marker", "sceneops_msgs/msg/SceneMarker", {"data": "x"}, T0 + 10**9)
    )
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert violations(report) == {
        ("I-37", "canonical_identifiers"),
        ("I-37", "sceneops_records"),
        ("I-37", "standard_messages"),
    }
    assert len(report.violations) == 4


def test_origin_metadata_is_optional_and_not_interpreted(tmp_path: Path) -> None:
    rec = conforming()
    rec.metadata = []
    without = check_l1_recording(rec.write(tmp_path / "a.mcap"))
    rec.metadata = [("sceneops.acquisition_origin", {"source_format": "nuscenes"})]
    with_origin = check_l1_recording(rec.write(tmp_path / "b.mcap"))

    assert without.conforms and with_origin.conforms
    assert without.acquisition_origin is None
    assert without.channels == with_origin.channels
    assert without.message_stream_sha256 == with_origin.message_stream_sha256


def test_duplicate_origin_records(tmp_path: Path) -> None:
    rec = conforming()
    rec.metadata.append(("sceneops.acquisition_origin", {"tool": "other"}))
    report = check_l1_recording(rec.write(tmp_path / "r.mcap"))
    assert violations(report) == {("R12", "acquisition_origin")}


def test_encoding_profile(tmp_path: Path) -> None:
    rec = Recording(
        messages=[Msg("/events", "std_msgs/msg/String", b'{"a": 1}', T0)],
        message_encoding="json",
        profile="",
    )
    path = rec.write(tmp_path / "r.mcap")
    assert ("R3", "ros2_encoding_profile") in violations(check_l1_recording(path))
    assert check_l1_recording(path, require_ros2_profile=False).conforms


def test_truncated_recording_is_not_finalized(tmp_path: Path) -> None:
    data = conforming().write(tmp_path / "ok.mcap").read_bytes()
    truncated = tmp_path / "truncated.mcap"
    truncated.write_bytes(data[: len(data) // 2])
    report = check_l1_recording(truncated)
    assert ("finalized", "readable") in violations(report)
    assert ("finalized", "footer") in violations(report)


def test_cli_check_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = conforming().write(tmp_path / "ok.mcap")
    assert cli_main(["check", "--mcap-path", str(ok)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["conforms"] is True
    assert report["channels"]["/lidar/top/points"]["message_count"] == 3

    bad = conforming()
    bad.messages = [m for m in bad.messages if m.topic != "/tf_static"]
    path = bad.write(tmp_path / "bad.mcap")
    assert cli_main(["check", "--mcap-path", str(path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["conforms"] is False
    assert {v["requirement"] for v in report["violations"]} == {"R9"}


def test_publication_facts_do_not_depend_on_origin_metadata(tmp_path: Path) -> None:
    """The publisher (and REGISTER_ROBOT_RUN, which re-derives the same
    facts) sees only messages: origin metadata changes the recording's
    checksum, never its channels or extent."""
    from sceneops_recording import derive_mcap_facts

    rec = conforming()
    plain = rec.write(tmp_path / "plain.mcap")
    rec.metadata = [("sceneops.acquisition_origin", {"source_format": "nuscenes"})]
    with_origin = rec.write(tmp_path / "origin.mcap")

    def facts(path: Path):
        with path.open("rb") as stream:
            return derive_mcap_facts(stream, source_clock="mcap_log_time")

    assert plain.read_bytes() != with_origin.read_bytes()
    assert facts(plain) == facts(with_origin)
