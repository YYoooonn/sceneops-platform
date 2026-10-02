"""RobotRunManifest v1 contract + canonical serialization (ADR-007 §8, I-13)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.robots.manifest import (
    ROBOT_RUN_MANIFEST_SCHEMA_V1,
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    ChannelFact,
    NonCanonicalRobotRunManifestError,
    RecordingFormat,
    RecordingRef,
    RobotRunManifest,
    RobotRunManifestError,
    UnsupportedRobotRunManifestVersionError,
    load_canonical_robot_run_manifest,
)

_CHECKSUM = "sha256:" + "ab" * 32


def _manifest(**overrides) -> RobotRunManifest:
    values = dict(
        run_id="run-001",
        robot_id="robot-001",
        robot_platform="nuscenes-can-replay",
        started_at=datetime(2026, 10, 2, 5, 10, 2, tzinfo=UTC),
        ended_at=datetime(2026, 10, 2, 5, 12, 44, 123456, tzinfo=UTC),
        recording=RecordingRef(
            format=RecordingFormat.MCAP,
            uri="s3://sceneops/artifacts/robot_runs/run-001/recording.mcap",
            checksum=_CHECKSUM,
            size_bytes=1_048_576,
        ),
        capture=CaptureInfo(
            source=CaptureSource(
                kind=CaptureSourceKind.KAFKA, topics=["robot.telemetry.v1"]
            ),
            source_clock="mcap_log_time",
        ),
        channels=[
            ChannelFact(
                topic="/imu",
                message_encoding="cdr",
                schema_name="sensor_msgs/msg/Imu",
                schema_encoding="ros2msg",
                message_count=10,
            ),
            ChannelFact(
                topic="/odom",
                message_encoding="cdr",
                schema_name="nav_msgs/msg/Odometry",
                schema_encoding="ros2msg",
                message_count=1180,
            ),
        ],
    )
    values.update(overrides)
    return RobotRunManifest(**values)


class TestCanonicalSerialization:
    def test_exact_canonical_bytes(self) -> None:
        data = _manifest().to_canonical_bytes()
        assert data == (
            b'{"capture":{"source":{"kind":"kafka","topics":["robot.telemetry.v1"]},'
            b'"source_clock":"mcap_log_time"},'
            b'"channels":[{"message_count":10,"message_encoding":"cdr",'
            b'"schema_encoding":"ros2msg","schema_name":"sensor_msgs/msg/Imu",'
            b'"topic":"/imu"},{"message_count":1180,"message_encoding":"cdr",'
            b'"schema_encoding":"ros2msg","schema_name":"nav_msgs/msg/Odometry",'
            b'"topic":"/odom"}],'
            b'"ended_at":"2026-10-02T05:12:44.123456Z",'
            b'"recording":{"checksum":"' + _CHECKSUM.encode() + b'","format":"mcap",'
            b'"size_bytes":1048576,'
            b'"uri":"s3://sceneops/artifacts/robot_runs/run-001/recording.mcap"},'
            b'"robot_id":"robot-001","robot_platform":"nuscenes-can-replay",'
            b'"run_id":"run-001","schema_version":"sceneops.robot_run_manifest/v1",'
            b'"started_at":"2026-10-02T05:10:02.000000Z"}'
        )

    def test_identical_inputs_produce_byte_identical_bytes(self) -> None:
        assert _manifest().to_canonical_bytes() == _manifest().to_canonical_bytes()

    def test_no_whitespace_no_trailing_newline_utf8(self) -> None:
        data = _manifest(robot_platform="로봇-플랫폼").to_canonical_bytes()
        assert not data.endswith(b"\n")
        assert b": " not in data and b", " not in data
        # non-ASCII emitted as UTF-8, never \u-escaped
        assert "로봇-플랫폼".encode() in data
        assert b"\\u" not in data

    def test_absent_robot_platform_is_emitted_as_null(self) -> None:
        payload = json.loads(_manifest(robot_platform=None).to_canonical_bytes())
        assert payload["robot_platform"] is None

    def test_non_kafka_topics_emitted_as_null(self) -> None:
        manifest = _manifest(
            capture=CaptureInfo(
                source=CaptureSource(kind=CaptureSourceKind.FILE),
                source_clock="mcap_log_time",
            )
        )
        payload = json.loads(manifest.to_canonical_bytes())
        assert payload["capture"]["source"] == {"kind": "file", "topics": None}

    def test_keys_sorted_at_every_level(self) -> None:
        def _check(value) -> None:
            if isinstance(value, dict):
                assert list(value) == sorted(value)
                for child in value.values():
                    _check(child)
            elif isinstance(value, list):
                for child in value:
                    _check(child)

        _check(json.loads(_manifest().to_canonical_bytes()))

    def test_canonical_json_rejects_nan(self) -> None:
        with pytest.raises(ValueError):
            canonical_json_bytes({"x": float("nan")})


class TestTimestampNormalization:
    def test_non_utc_offset_normalized_to_utc_z(self) -> None:
        kst = timezone(timedelta(hours=9))
        manifest = _manifest(
            started_at=datetime(2026, 10, 2, 14, 10, 2, 5, tzinfo=kst),
            ended_at=datetime(2026, 10, 2, 14, 12, 0, tzinfo=kst),
        )
        payload = json.loads(manifest.to_canonical_bytes())
        assert payload["started_at"] == "2026-10-02T05:10:02.000005Z"
        assert payload["ended_at"] == "2026-10-02T05:12:00.000000Z"

    def test_equal_instants_in_different_zones_serialize_identically(self) -> None:
        kst = timezone(timedelta(hours=9))
        a = _manifest(started_at=datetime(2026, 10, 2, 14, 10, 2, tzinfo=kst))
        b = _manifest(started_at=datetime(2026, 10, 2, 5, 10, 2, tzinfo=UTC))
        assert a.to_canonical_bytes() == b.to_canonical_bytes()

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _manifest(started_at=datetime(2026, 10, 2, 5, 10, 2))

    def test_ended_before_started_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ended_at must be >= started_at"):
            _manifest(
                started_at=datetime(2026, 10, 2, 6, tzinfo=UTC),
                ended_at=datetime(2026, 10, 2, 5, tzinfo=UTC),
            )


class TestSchemaValidation:
    def test_round_trip(self) -> None:
        manifest = _manifest()
        loaded = load_canonical_robot_run_manifest(manifest.to_canonical_bytes())
        assert loaded == manifest

    @pytest.mark.parametrize(
        "path",
        [
            ("dataset_id",),
            ("generated_at",),
            ("metadata",),
            ("recording", "media_type"),
            ("capture", "partition"),
            ("capture", "source", "offset"),
        ],
    )
    def test_unknown_fields_rejected(self, path: tuple[str, ...]) -> None:
        payload = json.loads(_manifest().to_canonical_bytes())
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = "x"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            RobotRunManifest.model_validate(payload)
        with pytest.raises(RobotRunManifestError):
            load_canonical_robot_run_manifest(canonical_json_bytes(payload))

    def test_unknown_schema_version_rejected(self) -> None:
        payload = json.loads(_manifest().to_canonical_bytes())
        payload["schema_version"] = "sceneops.robot_run_manifest/v2"
        with pytest.raises(UnsupportedRobotRunManifestVersionError):
            load_canonical_robot_run_manifest(canonical_json_bytes(payload))

    def test_kafka_requires_sorted_unique_topics(self) -> None:
        with pytest.raises(ValidationError, match="required for kind=kafka"):
            CaptureSource(kind=CaptureSourceKind.KAFKA)
        with pytest.raises(ValidationError, match="sorted and unique"):
            CaptureSource(kind=CaptureSourceKind.KAFKA, topics=["b", "a"])
        with pytest.raises(ValidationError, match="only allowed for kind=kafka"):
            CaptureSource(kind=CaptureSourceKind.ROS2_BAG, topics=["a"])

    def test_channels_must_be_sorted_unique_and_non_empty(self) -> None:
        channels = _manifest().channels
        with pytest.raises(ValidationError, match="sorted by topic"):
            _manifest(channels=list(reversed(channels)))
        with pytest.raises(ValidationError, match="sorted by topic"):
            _manifest(channels=[channels[0], channels[0]])
        with pytest.raises(ValidationError):
            _manifest(channels=[])

    @pytest.mark.parametrize(
        "field,value",
        [
            ("run_id", "../escape"),
            ("run_id", "a/b"),
            ("run_id", "x" * 101),
            ("robot_id", ""),
        ],
    )
    def test_identifiers_must_be_path_safe(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError):
            _manifest(**{field: value})

    def test_integers_are_strict(self) -> None:
        payload = json.loads(_manifest().to_canonical_bytes())
        payload["recording"]["size_bytes"] = "1048576"
        with pytest.raises(ValidationError):
            RobotRunManifest.model_validate(payload)
        payload["recording"]["size_bytes"] = 1048576.0
        with pytest.raises(ValidationError):
            RobotRunManifest.model_validate(payload)

    def test_checksum_format_enforced(self) -> None:
        with pytest.raises(ValidationError):
            RecordingRef(
                format=RecordingFormat.MCAP, uri="u", checksum="md5:x", size_bytes=1
            )

    def test_schema_version_constant(self) -> None:
        assert _manifest().schema_version == ROBOT_RUN_MANIFEST_SCHEMA_V1


class TestCanonicalFormEnforcement:
    """Bytes that parse to a valid, semantically equal manifest are still
    rejected unless they are exactly the canonical serialization."""

    def _payload(self) -> dict:
        return json.loads(_manifest().to_canonical_bytes())

    def test_pretty_printed_rejected(self) -> None:
        data = json.dumps(self._payload(), indent=2, sort_keys=True).encode()
        with pytest.raises(NonCanonicalRobotRunManifestError):
            load_canonical_robot_run_manifest(data)

    def test_trailing_newline_rejected(self) -> None:
        with pytest.raises(NonCanonicalRobotRunManifestError):
            load_canonical_robot_run_manifest(_manifest().to_canonical_bytes() + b"\n")

    def test_unsorted_keys_rejected(self) -> None:
        payload = self._payload()
        data = json.dumps(
            dict(reversed(list(payload.items()))), separators=(",", ":")
        ).encode()
        with pytest.raises(NonCanonicalRobotRunManifestError):
            load_canonical_robot_run_manifest(data)

    def test_non_canonical_timestamp_rejected(self) -> None:
        payload = self._payload()
        payload["started_at"] = "2026-10-02T05:10:02Z"  # missing 6 fractional digits
        with pytest.raises(NonCanonicalRobotRunManifestError):
            load_canonical_robot_run_manifest(canonical_json_bytes(payload))

    def test_escaped_non_ascii_rejected(self) -> None:
        manifest = _manifest(robot_platform="로봇")
        payload = json.loads(manifest.to_canonical_bytes())
        data = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        with pytest.raises(NonCanonicalRobotRunManifestError):
            load_canonical_robot_run_manifest(data)

    def test_omitted_null_field_rejected(self) -> None:
        payload = json.loads(_manifest(robot_platform=None).to_canonical_bytes())
        del payload["robot_platform"]
        with pytest.raises(NonCanonicalRobotRunManifestError):
            load_canonical_robot_run_manifest(canonical_json_bytes(payload))

    def test_bom_rejected(self) -> None:
        with pytest.raises(RobotRunManifestError):
            load_canonical_robot_run_manifest(
                b"\xef\xbb\xbf" + _manifest().to_canonical_bytes()
            )

    def test_not_json_rejected(self) -> None:
        with pytest.raises(RobotRunManifestError):
            load_canonical_robot_run_manifest(b"\xff\xfe not json")
