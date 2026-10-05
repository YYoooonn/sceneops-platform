"""CaptureReceipt v1 schema (ADR-008 §4.2 A1): round-trip, strict validation,
canonical-form enforcement."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sceneops_core.robots.capture_receipt import (
    CAPTURE_RECEIPT_SCHEMA_V1,
    CaptureReceipt,
    CaptureReceiptError,
    FinalizationReason,
    NonCanonicalCaptureReceiptError,
    UnsupportedCaptureReceiptVersionError,
    load_canonical_capture_receipt,
)

_CHECKSUM = "sha256:" + "ab" * 32


def _payload(**overrides) -> dict:
    payload = {
        "schema_version": CAPTURE_RECEIPT_SCHEMA_V1,
        "run_id": "run-001",
        "robot_id": "robot-001",
        "robot_platform": "nuscenes-can-replay",
        "recording": {
            "file": "run-001_0.mcap",
            "format": "mcap",
            "checksum": _CHECKSUM,
            "size_bytes": 1234,
        },
        "capture": {
            "source": {"kind": "kafka", "topics": ["sceneops.robot.telemetry.v1"]},
            "source_clock": "mcap_log_time",
        },
        "message_count": 5,
        "per_channel_counts": {"/vehicle/imu": 2, "/vehicle/odom": 3},
        "finalization": {
            "reason": "explicit_run_end",
            "finalized_at": "2026-10-05T12:00:00.123456Z",
        },
        "kafka": {
            "partition": 0,
            "first_offset": 0,
            "last_offset": 6,
            "first_sequence": 0,
            "last_sequence": 4,
        },
    }
    payload.update(overrides)
    return payload


def _receipt(**overrides) -> CaptureReceipt:
    return CaptureReceipt.model_validate(_payload(**overrides))


def test_round_trip_is_byte_stable() -> None:
    receipt = _receipt()
    data = receipt.to_canonical_bytes()

    loaded = load_canonical_capture_receipt(data)

    assert loaded == receipt
    assert loaded.to_canonical_bytes() == data
    assert loaded.finalization.reason is FinalizationReason.EXPLICIT_RUN_END
    assert loaded.finalization.finalized_at == datetime(
        2026, 10, 5, 12, 0, 0, 123456, tzinfo=UTC
    )


def test_robot_platform_is_optional() -> None:
    receipt = _receipt(robot_platform=None)
    assert (
        load_canonical_capture_receipt(receipt.to_canonical_bytes()).robot_platform
        is None
    )


@pytest.mark.parametrize("reason", [r.value for r in FinalizationReason])
def test_every_finalization_reason_round_trips(reason: str) -> None:
    receipt = _receipt(
        finalization={"reason": reason, "finalized_at": "2026-10-05T12:00:00.000000Z"}
    )
    assert load_canonical_capture_receipt(receipt.to_canonical_bytes()) == receipt


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": "../escape"},
        {"robot_id": ""},
        {"message_count": 0, "per_channel_counts": {"/a": 1}},
        {"message_count": 6},  # does not equal the per-channel sum
        {"per_channel_counts": {}},
        {"per_channel_counts": {"/a": 0, "/b": 5}},
        {
            "recording": {
                "file": "../run-001_0.mcap",
                "format": "mcap",
                "checksum": _CHECKSUM,
                "size_bytes": 1,
            }
        },
        {
            "recording": {
                "file": "run-001_0.bag",
                "format": "mcap",
                "checksum": _CHECKSUM,
                "size_bytes": 1,
            }
        },
        {
            "recording": {
                "file": "run-001_0.mcap",
                "format": "mcap",
                "checksum": "md5:abc",
                "size_bytes": 1,
            }
        },
        {
            "recording": {
                "file": "run-001_0.mcap",
                "format": "mcap",
                "checksum": _CHECKSUM,
                "size_bytes": 0,
            }
        },
        {
            "capture": {
                "source": {"kind": "kafka", "topics": ["b", "a"]},
                "source_clock": "mcap_log_time",
            }
        },
        {"capture": {"source": {"kind": "file"}, "source_clock": "mcap_log_time"}},
        {
            "finalization": {
                "reason": "because",
                "finalized_at": "2026-10-05T12:00:00.000000Z",
            }
        },
        {"finalization": {"reason": "manual", "finalized_at": "2026-10-05T12:00:00"}},
        {
            "kafka": {
                "partition": 0,
                "first_offset": 5,
                "last_offset": 4,
                "first_sequence": 0,
                "last_sequence": 4,
            }
        },
        {
            "kafka": {
                "partition": 0,
                "first_offset": 0,
                "last_offset": 4,
                "first_sequence": 3,
                "last_sequence": 2,
            }
        },
        {"unexpected": 1},
    ],
)
def test_invalid_receipts_are_rejected(overrides: dict) -> None:
    payload = _payload(**overrides)
    with pytest.raises(CaptureReceiptError):
        load_canonical_capture_receipt(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "data",
    [b"", b"\xff\xfe", b"not json", b"[]", b'"x"', b"{", b"null"],
)
def test_malformed_bytes_are_rejected(data: bytes) -> None:
    with pytest.raises(CaptureReceiptError):
        load_canonical_capture_receipt(data)


def test_unknown_schema_version_is_rejected() -> None:
    payload = _payload(schema_version="sceneops.capture_receipt/v2")
    with pytest.raises(UnsupportedCaptureReceiptVersionError):
        load_canonical_capture_receipt(json.dumps(payload).encode())


def test_missing_schema_version_is_rejected() -> None:
    payload = _payload()
    del payload["schema_version"]
    with pytest.raises(UnsupportedCaptureReceiptVersionError):
        load_canonical_capture_receipt(json.dumps(payload).encode())


def test_non_canonical_bytes_are_rejected() -> None:
    canonical = _receipt().to_canonical_bytes()
    pretty = json.dumps(json.loads(canonical), indent=2).encode()
    with pytest.raises(NonCanonicalCaptureReceiptError):
        load_canonical_capture_receipt(pretty)
    with pytest.raises(NonCanonicalCaptureReceiptError):
        load_canonical_capture_receipt(canonical + b"\n")


def test_truncated_receipt_is_rejected() -> None:
    canonical = _receipt().to_canonical_bytes()
    with pytest.raises(CaptureReceiptError):
        load_canonical_capture_receipt(canonical[: len(canonical) // 2])


def test_receipt_holds_no_domain_semantics() -> None:
    """Acquisition/publication inputs only: nothing a Scene, Episode or
    RobotRun registration would read."""
    assert set(CaptureReceipt.model_fields) == {
        "schema_version",
        "run_id",
        "robot_id",
        "robot_platform",
        "recording",
        "capture",
        "message_count",
        "per_channel_counts",
        "finalization",
        "kafka",
    }
