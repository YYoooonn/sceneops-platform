"""Capture receipt written atomically with finalize (ADR-008 §4.2 A1, Phase
12.2): the receipt lands in the partial bag before the rename, so a finalized
bag never exists without it; retries converge on the bag of record; conflicts
fail loudly and never commit Kafka offsets.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sceneops_core.robots.capture_receipt import (  # noqa: E402
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceiptError,
    FinalizationReason,
    load_canonical_capture_receipt,
)
from sceneops_core.streaming import (  # noqa: E402
    ConsumedTelemetryEnvelope,
    EnvelopeEncoding,
    TelemetryEnvelope,
)

import capture_consumer as capture_consumer_module  # noqa: E402
from capture_consumer import CAPTURE_CONSUMER_GROUP_ID, run_capture  # noqa: E402
from finalize import FinalBagExistsError, final_bag_path, partial_bag_path  # noqa: E402
from receipt import CaptureReceiptConflictError  # noqa: E402

TOPIC = "sceneops.robot.telemetry.v1"


def _envelope(**overrides) -> TelemetryEnvelope:
    fields = dict(
        robot_id="robot-1",
        robot_run_id="run-1",
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_000_000_000,
        ingest_timestamp_ns=2_000_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=b"\x00\x01\x00\x00" + b"x" * 16,
    )
    fields.update(overrides)
    return TelemetryEnvelope(**fields)


def _queue(run_id: str, count: int = 3, *, first_offset: int = 10):
    return [
        ConsumedTelemetryEnvelope(
            envelope=_envelope(robot_run_id=run_id, sequence_number=i),
            key=run_id.encode(),
            topic=TOPIC,
            partition=2,
            offset=first_offset + i,
        )
        for i in range(count)
    ]


class _FakeConsumer:
    def __init__(self, queue) -> None:
        self._queue = list(queue)
        self.committed = False

    async def poll(self, timeout_seconds: float):
        return self._queue.pop(0) if self._queue else None

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        pass


def _install(monkeypatch, queue) -> list[_FakeConsumer]:
    created: list[_FakeConsumer] = []

    def factory(*, settings, group_id, auto_offset_reset, enable_auto_commit):
        assert group_id.startswith(CAPTURE_CONSUMER_GROUP_ID + "-")
        consumer = _FakeConsumer(queue)
        created.append(consumer)
        return consumer

    monkeypatch.setattr(capture_consumer_module, "KafkaTelemetryConsumer", factory)
    return created


def _capture(tmp_path, run_id="run-1", **overrides):
    kwargs = dict(
        settings=object(),
        robot_id="robot-1",
        robot_run_id=run_id,
        output_root=tmp_path,
        stop_condition=lambda count: count >= 3,
    )
    kwargs.update(overrides)
    return asyncio.run(run_capture(**kwargs))


def _receipt(tmp_path, run_id="run-1"):
    path = final_bag_path(tmp_path, run_id) / CAPTURE_RECEIPT_FILENAME
    return load_canonical_capture_receipt(path.read_bytes())


def test_finalized_bag_contains_mcap_and_receipt(tmp_path, monkeypatch) -> None:
    _install(monkeypatch, _queue("run-1"))

    result = _capture(tmp_path, robot_platform="platform-x")

    final = final_bag_path(tmp_path, "run-1")
    assert sorted(p.name for p in final.iterdir()) == [
        CAPTURE_RECEIPT_FILENAME,
        "run-1_0.mcap",
    ]
    assert result.receipt_path == final / CAPTURE_RECEIPT_FILENAME
    receipt = _receipt(tmp_path)
    mcap_bytes = result.path.read_bytes()
    assert receipt.run_id == "run-1"
    assert receipt.robot_id == "robot-1"
    assert receipt.robot_platform == "platform-x"
    assert receipt.recording.file == "run-1_0.mcap"
    assert receipt.recording.checksum == "sha256:" + hashlib.sha256(mcap_bytes).hexdigest()
    assert receipt.recording.size_bytes == len(mcap_bytes)
    assert receipt.recording.checksum == "sha256:" + result.sha256
    assert receipt.message_count == 3
    assert receipt.per_channel_counts == {"/vehicle/odom": 3}
    assert receipt.capture.source.topics == [TOPIC]
    assert receipt.capture.source_clock == "mcap_log_time"
    assert receipt.kafka.partition == 2
    assert (receipt.kafka.first_offset, receipt.kafka.last_offset) == (10, 12)
    assert (receipt.kafka.first_sequence, receipt.kafka.last_sequence) == (0, 2)
    assert not partial_bag_path(tmp_path, "run-1").exists()


def test_receipt_platform_defaults_to_null(tmp_path, monkeypatch) -> None:
    _install(monkeypatch, _queue("run-1"))
    _capture(tmp_path)
    assert _receipt(tmp_path).robot_platform is None


@pytest.mark.parametrize(
    "stop, reason",
    [
        (lambda count: FinalizationReason.MAX_MESSAGES if count >= 3 else None,
         FinalizationReason.MAX_MESSAGES),
        (lambda count: FinalizationReason.IDLE_TIMEOUT if count >= 3 else None,
         FinalizationReason.IDLE_TIMEOUT),
        (lambda count: count >= 3, FinalizationReason.MANUAL),
    ],
    ids=["max_messages", "idle_timeout", "plain-bool"],
)
def test_finalization_reason_comes_from_the_stop_condition(
    tmp_path, monkeypatch, stop, reason
) -> None:
    _install(monkeypatch, _queue("run-1"))
    _capture(tmp_path, stop_condition=stop)
    assert _receipt(tmp_path).finalization.reason is reason


def test_explicit_run_end_reason(tmp_path, monkeypatch) -> None:
    from sceneops_core.streaming import RunEventType, build_control_envelope

    run_id = "run-1"
    queue = _queue(run_id)
    queue.append(
        ConsumedTelemetryEnvelope(
            envelope=build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id="robot-1",
                robot_run_id=run_id,
                sequence_number=0,
            ),
            key=run_id.encode(),
            topic=TOPIC,
            partition=2,
            offset=13,
        )
    )
    _install(monkeypatch, queue)

    _capture(tmp_path, stop_condition=lambda count: False, stop_on_run_end=True)

    receipt = _receipt(tmp_path)
    assert receipt.finalization.reason is FinalizationReason.EXPLICIT_RUN_END
    # The control event is not recorded, but its offset is part of the span.
    assert receipt.message_count == 3
    assert receipt.kafka.last_offset == 13


def test_crash_before_rename_exposes_no_finalized_capture(
    tmp_path, monkeypatch
) -> None:
    """Boundary: receipt written into .partial, then the process dies before
    the atomic rename. Nothing is finalized, nothing is committed, and a
    publisher scanning finalized bags finds nothing."""
    created = _install(monkeypatch, _queue("run-1"))

    def crash(output_root, robot_run_id):
        raise RuntimeError("simulated crash before rename")

    monkeypatch.setattr(capture_consumer_module, "finalize_bag", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _capture(tmp_path)

    assert not final_bag_path(tmp_path, "run-1").exists()
    assert created[0].committed is False
    # The receipt exists only inside the partial directory, next to its MCAP.
    assert (partial_bag_path(tmp_path, "run-1") / CAPTURE_RECEIPT_FILENAME).is_file()


def test_crash_after_rename_leaves_recording_and_receipt_together(
    tmp_path, monkeypatch
) -> None:
    """Boundary D: killed after finalize, before the Kafka commit. The
    finalized bag is whole -- receipt included -- and recoverable."""
    created = _install(monkeypatch, _queue("run-1"))

    async def crash_commit(self):
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(_FakeConsumer, "commit", crash_commit)
    with pytest.raises(RuntimeError, match="before commit"):
        _capture(tmp_path)
    assert created[0].committed is False

    final = final_bag_path(tmp_path, "run-1")
    assert (final / "run-1_0.mcap").is_file()
    receipt = _receipt(tmp_path)
    assert receipt.recording.checksum == "sha256:" + hashlib.sha256(
        (final / "run-1_0.mcap").read_bytes()
    ).hexdigest()


def test_stale_partial_with_receipt_is_discarded_on_retry(
    tmp_path, monkeypatch
) -> None:
    stale = partial_bag_path(tmp_path, "run-1")
    stale.mkdir(parents=True)
    (stale / CAPTURE_RECEIPT_FILENAME).write_bytes(b"{stale receipt from a dead attempt")
    _install(monkeypatch, _queue("run-1"))

    _capture(tmp_path)

    assert _receipt(tmp_path).message_count == 3
    assert not stale.exists()


def test_retry_after_finalize_converges_on_the_existing_receipt(
    tmp_path, monkeypatch
) -> None:
    _install(monkeypatch, _queue("run-1"))
    first = _capture(tmp_path, robot_platform="platform-x")
    receipt_bytes = first.receipt_path.read_bytes()

    created = _install(monkeypatch, _queue("run-1"))
    second = _capture(tmp_path, robot_platform="platform-x")

    assert created[0].committed is True
    assert second.receipt_path == first.receipt_path
    # The first finalized bag (and its receipt) is the recording of record.
    assert second.receipt_path.read_bytes() == receipt_bytes
    assert second.sha256 == first.sha256


@pytest.mark.parametrize(
    "override",
    [{"robot_platform": "platform-other"}, {"robot_platform": None}],
    ids=["different-platform", "dropped-platform"],
)
def test_retry_with_conflicting_metadata_fails_loudly(
    tmp_path, monkeypatch, override
) -> None:
    _install(monkeypatch, _queue("run-1"))
    first = _capture(tmp_path, robot_platform="platform-x")
    before = (first.path.read_bytes(), first.receipt_path.read_bytes())

    created = _install(monkeypatch, _queue("run-1"))
    with pytest.raises(CaptureReceiptConflictError, match="robot_platform"):
        _capture(tmp_path, **override)

    assert created[0].committed is False
    assert (first.path.read_bytes(), first.receipt_path.read_bytes()) == before


def test_retry_with_conflicting_robot_identity_fails_loudly(
    tmp_path, monkeypatch
) -> None:
    _install(monkeypatch, _queue("run-1"))
    first = _capture(tmp_path)

    queue = [
        ConsumedTelemetryEnvelope(
            envelope=_envelope(robot_id="robot-2", robot_run_id="run-1", sequence_number=i),
            key=b"run-1",
            topic=TOPIC,
            partition=2,
            offset=10 + i,
        )
        for i in range(3)
    ]
    created = _install(monkeypatch, queue)
    with pytest.raises(CaptureReceiptConflictError, match="robot_id"):
        _capture(tmp_path, robot_id="robot-2")

    assert created[0].committed is False
    assert _receipt(tmp_path).robot_id == "robot-1"
    assert first.path.exists()


def test_retry_against_a_corrupt_existing_receipt_fails_loudly(
    tmp_path, monkeypatch
) -> None:
    _install(monkeypatch, _queue("run-1"))
    first = _capture(tmp_path)
    first.receipt_path.write_bytes(first.receipt_path.read_bytes()[:50])

    created = _install(monkeypatch, _queue("run-1"))
    with pytest.raises(CaptureReceiptError):
        _capture(tmp_path)
    assert created[0].committed is False


def test_retry_against_a_receipt_that_misdescribes_its_bytes_fails_loudly(
    tmp_path, monkeypatch
) -> None:
    _install(monkeypatch, _queue("run-1"))
    first = _capture(tmp_path)
    payload = json.loads(first.receipt_path.read_bytes())
    payload["recording"]["checksum"] = "sha256:" + "0" * 64
    from sceneops_core.robots.capture_receipt import CaptureReceipt

    first.receipt_path.write_bytes(
        CaptureReceipt.model_validate(payload).to_canonical_bytes()
    )

    created = _install(monkeypatch, _queue("run-1"))
    with pytest.raises(CaptureReceiptConflictError, match="checksum"):
        _capture(tmp_path)
    assert created[0].committed is False


def test_legacy_bag_without_receipt_still_converges_without_gaining_one(
    tmp_path, monkeypatch
) -> None:
    """A bag finalized before receipts existed is never retro-fitted: a
    receipt is only ever written atomically with finalize."""
    _install(monkeypatch, _queue("run-1"))
    first = _capture(tmp_path)
    first.receipt_path.unlink()

    created = _install(monkeypatch, _queue("run-1"))
    second = _capture(tmp_path)

    assert created[0].committed is True
    assert second.receipt_path is None
    assert not (final_bag_path(tmp_path, "run-1") / CAPTURE_RECEIPT_FILENAME).exists()


def test_different_recorded_content_still_fails_with_final_bag_exists(
    tmp_path, monkeypatch
) -> None:
    _install(monkeypatch, _queue("run-1"))
    _capture(tmp_path)

    changed = [
        ConsumedTelemetryEnvelope(
            envelope=_envelope(
                robot_run_id="run-1", sequence_number=i, payload=b"\x00\x01\x00\x00" + b"y" * 16
            ),
            key=b"run-1",
            topic=TOPIC,
            partition=2,
            offset=10 + i,
        )
        for i in range(3)
    ]
    created = _install(monkeypatch, changed)
    with pytest.raises(FinalBagExistsError):
        _capture(tmp_path)
    assert created[0].committed is False


def test_invalid_run_id_fails_before_finalize(tmp_path, monkeypatch) -> None:
    """The receipt refuses an identity the manifest would refuse, so such a
    capture is never finalized into something that cannot be published."""
    _install(monkeypatch, _queue("bad run!"))
    with pytest.raises(ValueError):
        _capture(tmp_path, run_id="bad run!")
    assert not final_bag_path(tmp_path, "bad run!").exists()
