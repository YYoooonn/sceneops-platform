#!/usr/bin/env python3
"""Phase 7.0 study: capture-side benchmark runner.

Runs INSIDE the ros2 container (needs rosbag2_py, via
ros2/capture/mcap_writer.py). Real Kafka, real MCAP writer/validate/
finalize -- reuses ros2/capture's frozen modules directly rather than
reimplementing them; the only thing this script varies is HOW the Kafka
consumer is constructed for the "partitionaware" mode (direct assign()
to one partition instead of capture_consumer.run_capture()'s
subscribe()-based, run-scoped-consumer-group approach). Nothing in
ros2/capture/ is modified.

Subcommands:

    runscoped        current baseline (A): capture_consumer.run_capture()
                      unmodified, one robot_run_id.
    runscoped-batch   current baseline (A) applied sequentially to many
                      robot_run_ids in one process (avoids per-run
                      container-startup overhead skewing the multi-run
                      amplification measurement, section 5).
    partitionaware    prototype (B): same writer/validate/finalize
                      pipeline, but the consumer is assign()ed directly
                      to one known Kafka partition (seek to that
                      partition's own earliest offset) instead of
                      subscribe()-ing to the whole topic under a fresh
                      run-scoped group.

This is Phase 7.0 study tooling (category C evidence-gathering) -- not
a production capture-path change.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, "/workspace/capture")

from confluent_kafka import Consumer as ConfluentConsumer  # noqa: E402
from confluent_kafka import OFFSET_BEGINNING, TopicPartition  # noqa: E402

from sceneops_core.streaming import ConsumedTelemetryEnvelope  # noqa: E402
from sceneops_streaming import StreamingSettings  # noqa: E402
from sceneops_streaming.wire import decode_envelope  # noqa: E402

import capture_consumer  # noqa: E402
from capture_consumer import CAPTURE_CONSUMER_GROUP_ID, CaptureResult, run_capture  # noqa: E402
from finalize import finalize_bag, prepare_partial_bag_dir  # noqa: E402
from group_id import derive_capture_group_id  # noqa: E402
from mcap_writer import McapCaptureWriter  # noqa: E402
from validation import validate_mcap_file  # noqa: E402


def _peak_rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


async def _run_partition_aware_capture(
    *,
    settings: StreamingSettings,
    robot_id: str,
    robot_run_id: str,
    partition: int,
    output_root: Path,
    stop_condition,
    poll_timeout_seconds: float = 1.0,
) -> CaptureResult:
    """Prototype (B): direct assign() to one Kafka partition, seek to
    that partition's own earliest offset, and never touch any other
    partition -- unlike run_capture()'s subscribe(), which (as the sole
    group member) gets every partition of the topic assigned and must
    scan all of them. Reuses the same writer/validate/finalize pipeline
    run_capture() uses, via direct import -- only the consumer
    construction/positioning differs."""
    partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
    writer = McapCaptureWriter(bag_uri=str(partial_dir))
    run_filter = capture_consumer._RunFilter(robot_id=robot_id, robot_run_id=robot_run_id)
    tracker = capture_consumer._SequenceTracker()

    group_id = derive_capture_group_id(
        base=f"{CAPTURE_CONSUMER_GROUP_ID}-partition-aware-study",
        robot_run_id=robot_run_id,
    )
    topic = settings.telemetry_topic
    consumer = ConfluentConsumer(
        {
            "bootstrap.servers": settings.bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
        }
    )
    tp = TopicPartition(topic, partition, OFFSET_BEGINNING)
    consumer.assign([tp])

    first_offset: int | None = None
    last_offset: int | None = None

    try:
        try:
            while not stop_condition(writer.stats.message_count):
                msg = consumer.poll(poll_timeout_seconds)
                if msg is None:
                    continue
                if msg.error() is not None:
                    raise RuntimeError(f"Kafka consumer error: {msg.error()}")
                envelope = decode_envelope(headers=msg.headers(), value=msg.value())
                consumed = ConsumedTelemetryEnvelope(
                    envelope=envelope,
                    key=msg.key() or b"",
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                )
                if not run_filter.matches(consumed):
                    continue
                should_write = tracker.accept(
                    sequence_number=envelope.sequence_number, payload=envelope.payload
                )
                if not should_write:
                    continue
                writer.write_envelope(envelope)
                if first_offset is None:
                    first_offset = consumed.offset
                last_offset = consumed.offset
        finally:
            writer.close()

        mcap_path = writer.mcap_file_path()
        validate_mcap_file(
            mcap_path, expected_message_count=writer.stats.message_count
        )
        final_dir = finalize_bag(output_root, robot_run_id)
        final_mcap_path = final_dir / Path(mcap_path).name
        digest_hex = capture_consumer._sha256_file(final_mcap_path)

        result = CaptureResult(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            path=final_mcap_path,
            message_count=writer.stats.message_count,
            partition=run_filter.partition,
            first_offset=first_offset,
            last_offset=last_offset,
            first_sequence=tracker.first_sequence,
            last_sequence=tracker.last_sequence,
            sha256=digest_hex,
        )
        consumer.commit(asynchronous=False)
        return result
    finally:
        consumer.close()


async def _run_subscribed_sync_capture(
    *,
    settings: StreamingSettings,
    robot_id: str,
    robot_run_id: str,
    output_root: Path,
    stop_condition,
    poll_timeout_seconds: float = 1.0,
) -> CaptureResult:
    """Isolation probe: subscribe()-based, run-scoped-group consumption
    (architecturally identical to run_capture()/baseline A) but polled
    SYNCHRONOUSLY (no asyncio.to_thread per call) -- to separate "cost of
    consumer-group join/rebalance protocol" from "cost of
    KafkaTelemetryConsumer.poll()'s per-call asyncio.to_thread dispatch
    overhead", since baseline A pays both and prototype B (assign(),
    also synchronous) pays neither. Not a production code path."""
    partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
    writer = McapCaptureWriter(bag_uri=str(partial_dir))
    run_filter = capture_consumer._RunFilter(robot_id=robot_id, robot_run_id=robot_run_id)
    tracker = capture_consumer._SequenceTracker()

    group_id = derive_capture_group_id(
        base=f"{CAPTURE_CONSUMER_GROUP_ID}-subscribed-sync-study",
        robot_run_id=robot_run_id,
    )
    consumer = ConfluentConsumer(
        {
            "bootstrap.servers": settings.bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([settings.telemetry_topic])

    first_offset: int | None = None
    last_offset: int | None = None
    try:
        try:
            while not stop_condition(writer.stats.message_count):
                msg = consumer.poll(poll_timeout_seconds)
                if msg is None:
                    continue
                if msg.error() is not None:
                    raise RuntimeError(f"Kafka consumer error: {msg.error()}")
                envelope = decode_envelope(headers=msg.headers(), value=msg.value())
                consumed = ConsumedTelemetryEnvelope(
                    envelope=envelope,
                    key=msg.key() or b"",
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                )
                if not run_filter.matches(consumed):
                    continue
                should_write = tracker.accept(
                    sequence_number=envelope.sequence_number, payload=envelope.payload
                )
                if not should_write:
                    continue
                writer.write_envelope(envelope)
                if first_offset is None:
                    first_offset = consumed.offset
                last_offset = consumed.offset
        finally:
            writer.close()

        mcap_path = writer.mcap_file_path()
        validate_mcap_file(
            mcap_path, expected_message_count=writer.stats.message_count
        )
        final_dir = finalize_bag(output_root, robot_run_id)
        final_mcap_path = final_dir / Path(mcap_path).name
        digest_hex = capture_consumer._sha256_file(final_mcap_path)
        result = CaptureResult(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            path=final_mcap_path,
            message_count=writer.stats.message_count,
            partition=run_filter.partition,
            first_offset=first_offset,
            last_offset=last_offset,
            first_sequence=tracker.first_sequence,
            last_sequence=tracker.last_sequence,
            sha256=digest_hex,
        )
        consumer.commit(asynchronous=False)
        return result
    finally:
        consumer.close()


def cmd_subscribed_sync(args: argparse.Namespace) -> int:
    import asyncio

    settings = StreamingSettings(telemetry_topic=args.topic) if args.topic else StreamingSettings()
    t0 = time.monotonic()
    result = asyncio.run(
        _run_subscribed_sync_capture(
            settings=settings,
            robot_id=args.robot_id,
            robot_run_id=args.robot_run_id,
            output_root=Path(args.output_root),
            stop_condition=lambda count: count >= args.count,
            poll_timeout_seconds=args.poll_timeout,
        )
    )
    duration = time.monotonic() - t0
    print(
        json.dumps(
            {
                "mode": "subscribed-sync",
                "robot_run_id": args.robot_run_id,
                "message_count": result.message_count,
                "partition": result.partition,
                "duration_s": round(duration, 3),
                "msgs_per_s": round(result.message_count / duration, 1) if duration > 0 else None,
                "peak_rss_kb": _peak_rss_kb(),
            }
        )
    )
    return 0


def cmd_runscoped(args: argparse.Namespace) -> int:
    import asyncio

    settings = StreamingSettings(telemetry_topic=args.topic) if args.topic else StreamingSettings()
    group_id = derive_capture_group_id(
        base=CAPTURE_CONSUMER_GROUP_ID, robot_run_id=args.robot_run_id
    )
    t0 = time.monotonic()
    result = asyncio.run(
        run_capture(
            settings=settings,
            robot_id=args.robot_id,
            robot_run_id=args.robot_run_id,
            output_root=Path(args.output_root),
            stop_condition=lambda count: count >= args.count,
            poll_timeout_seconds=args.poll_timeout,
        )
    )
    duration = time.monotonic() - t0
    print(
        json.dumps(
            {
                "mode": "runscoped",
                "robot_run_id": args.robot_run_id,
                "message_count": result.message_count,
                "partition": result.partition,
                "first_offset": result.first_offset,
                "last_offset": result.last_offset,
                "duration_s": round(duration, 3),
                "msgs_per_s": round(result.message_count / duration, 1) if duration > 0 else None,
                "group_id": group_id,
                "peak_rss_kb": _peak_rss_kb(),
            }
        )
    )
    return 0


def cmd_runscoped_batch(args: argparse.Namespace) -> int:
    import asyncio

    settings = StreamingSettings(telemetry_topic=args.topic) if args.topic else StreamingSettings()
    run_ids = [f"{args.run_prefix}-{i:04d}" for i in range(args.num_runs)]
    rows = []
    total_t0 = time.monotonic()
    for robot_run_id in run_ids:
        t0 = time.monotonic()
        result = asyncio.run(
            run_capture(
                settings=settings,
                robot_id="robot-phase7-multirun",
                robot_run_id=robot_run_id,
                output_root=Path(args.output_root),
                stop_condition=lambda count: count >= args.per_run_count,
                poll_timeout_seconds=args.poll_timeout,
            )
        )
        duration = time.monotonic() - t0
        rows.append(
            {
                "robot_run_id": robot_run_id,
                "message_count": result.message_count,
                "partition": result.partition,
                "duration_s": round(duration, 3),
            }
        )
    total_duration = time.monotonic() - total_t0
    print(
        json.dumps(
            {
                "mode": "runscoped-batch",
                "num_runs": args.num_runs,
                "per_run_count": args.per_run_count,
                "total_duration_s": round(total_duration, 3),
                "peak_rss_kb": _peak_rss_kb(),
                "runs": rows,
            }
        )
    )
    return 0


def cmd_partitionaware(args: argparse.Namespace) -> int:
    import asyncio

    settings = StreamingSettings(telemetry_topic=args.topic) if args.topic else StreamingSettings()
    t0 = time.monotonic()
    result = asyncio.run(
        _run_partition_aware_capture(
            settings=settings,
            robot_id=args.robot_id,
            robot_run_id=args.robot_run_id,
            partition=args.partition,
            output_root=Path(args.output_root),
            stop_condition=lambda count: count >= args.count,
            poll_timeout_seconds=args.poll_timeout,
        )
    )
    duration = time.monotonic() - t0
    print(
        json.dumps(
            {
                "mode": "partitionaware",
                "robot_run_id": args.robot_run_id,
                "message_count": result.message_count,
                "partition": result.partition,
                "duration_s": round(duration, 3),
                "msgs_per_s": round(result.message_count / duration, 1) if duration > 0 else None,
                "peak_rss_kb": _peak_rss_kb(),
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--output-root", default="/data/tmp_phase7_bench")
    parser.add_argument("--poll-timeout", type=float, default=1.0)
    sub = parser.add_subparsers(dest="mode", required=True)

    p1 = sub.add_parser("runscoped")
    p1.add_argument("--robot-run-id", required=True)
    p1.add_argument("--robot-id", default="robot-phase7-target")
    p1.add_argument("--count", type=int, required=True)
    p1.set_defaults(func=cmd_runscoped)

    p2 = sub.add_parser("runscoped-batch")
    p2.add_argument("--num-runs", type=int, required=True)
    p2.add_argument("--per-run-count", type=int, required=True)
    p2.add_argument("--run-prefix", default="phase7-mr")
    p2.set_defaults(func=cmd_runscoped_batch)

    p3 = sub.add_parser("partitionaware")
    p3.add_argument("--robot-run-id", required=True)
    p3.add_argument("--robot-id", default="robot-phase7-target")
    p3.add_argument("--count", type=int, required=True)
    p3.add_argument("--partition", type=int, required=True)
    p3.set_defaults(func=cmd_partitionaware)

    p4 = sub.add_parser("subscribed-sync")
    p4.add_argument("--robot-run-id", required=True)
    p4.add_argument("--robot-id", default="robot-phase7-target")
    p4.add_argument("--count", type=int, required=True)
    p4.set_defaults(func=cmd_subscribed_sync)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
