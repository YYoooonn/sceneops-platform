#!/usr/bin/env python3
"""Phase 7.0 study: host-side Kafka telemetry producer helpers.

Runs on the HOST (uv run python) against a localhost-reachable broker --
same convention as scripts/e2e/smoke_streaming.py. Does not need
rosbag2_py, so it never needs the ros2 container. Real
TelemetryEnvelope/KafkaTelemetryProducer, real Kafka -- no mocking.

Three subcommands:

    noise     publish COUNT messages spread round-robin across
              NUM-RUNS distinct synthetic robot_run_ids (simulates
              accumulated multi-run topic history without touching
              any target run's own sequence numbering)
    run       publish one deterministic run's 0..COUNT-1 sequence
              under one robot_run_id, report the Kafka partition it
              landed on (via the delivery callback)
    multirun  publish NUM-RUNS runs interleaved round-robin, each its
              own 0..PER-RUN-COUNT-1 sequence, report every run's
              robot_run_id + partition

This is Phase 7.0 study tooling (category C evidence-gathering), not a
production capture-path change -- nothing here touches ros2/capture or
sceneops-streaming.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from confluent_kafka import Producer as ConfluentProducer

from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope
from sceneops_streaming import StreamingSettings
from sceneops_streaming.wire import encode_envelope

_DELIVERY_PARTITIONS: dict[str, int] = {}


def _make_producer(settings: StreamingSettings) -> ConfluentProducer:
    return ConfluentProducer(
        {
            "bootstrap.servers": settings.bootstrap_servers,
            "client.id": "phase7-study-producer",
            "acks": "all",
            "linger.ms": 5,
            "batch.num.messages": 10000,
            "queue.buffering.max.messages": 500000,
            "queue.buffering.max.kbytes": 1048576,
        }
    )


def _on_delivery(err, msg) -> None:
    if err is not None:
        raise RuntimeError(f"delivery failed: {err}")
    key = msg.key().decode("utf-8") if msg.key() else ""
    _DELIVERY_PARTITIONS[key] = msg.partition()


def _publish(
    producer: ConfluentProducer,
    *,
    topic: str,
    robot_id: str,
    robot_run_id: str,
    sequence_number: int,
    payload_bytes: int,
    channel: str = "/vehicle/odom",
    message_type: str = "nav_msgs/msg/Odometry",
) -> None:
    payload = bytes([0x00, 0x01, 0x00, 0x00]) + (b"x" * max(0, payload_bytes - 4))
    env = TelemetryEnvelope(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        channel=channel,
        message_type=message_type,
        source_timestamp_ns=1_700_000_000_000_000_000 + sequence_number,
        ingest_timestamp_ns=1_700_000_000_500_000_000 + sequence_number,
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=payload,
    )
    record = encode_envelope(env)
    while True:
        try:
            producer.produce(
                topic=topic,
                key=record.key,
                value=record.value,
                headers=record.headers,
                on_delivery=_on_delivery,
            )
            break
        except BufferError:
            producer.poll(0.5)
    producer.poll(0)


def cmd_noise(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    producer = _make_producer(settings)
    t0 = time.monotonic()
    for i in range(args.count):
        run_idx = i % args.num_runs
        robot_run_id = f"{args.run_prefix}-{run_idx:05d}"
        _publish(
            producer,
            topic=args.topic or settings.telemetry_topic,
            robot_id="robot-phase7-noise",
            robot_run_id=robot_run_id,
            sequence_number=i // args.num_runs,
            payload_bytes=args.payload_bytes,
        )
    remaining = producer.flush(180.0)
    duration = time.monotonic() - t0
    print(
        json.dumps(
            {
                "mode": "noise",
                "count": args.count,
                "num_runs": args.num_runs,
                "duration_s": round(duration, 3),
                "msgs_per_s": round(args.count / duration, 1) if duration > 0 else None,
                "undelivered": remaining,
            }
        )
    )
    return 0 if remaining == 0 else 1


def cmd_run(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    producer = _make_producer(settings)
    topic = args.topic or settings.telemetry_topic
    t0 = time.monotonic()
    for i in range(args.count):
        _publish(
            producer,
            topic=topic,
            robot_id=args.robot_id,
            robot_run_id=args.robot_run_id,
            sequence_number=i,
            payload_bytes=args.payload_bytes,
        )
    remaining = producer.flush(180.0)
    duration = time.monotonic() - t0
    print(
        json.dumps(
            {
                "mode": "run",
                "robot_run_id": args.robot_run_id,
                "count": args.count,
                "duration_s": round(duration, 3),
                "msgs_per_s": round(args.count / duration, 1) if duration > 0 else None,
                "partition": _DELIVERY_PARTITIONS.get(args.robot_run_id),
                "undelivered": remaining,
            }
        )
    )
    return 0 if remaining == 0 else 1


def cmd_multirun(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    producer = _make_producer(settings)
    topic = args.topic or settings.telemetry_topic
    run_ids = [f"{args.run_prefix}-{i:04d}" for i in range(args.num_runs)]
    t0 = time.monotonic()
    for seq in range(args.per_run_count):
        for robot_run_id in run_ids:
            _publish(
                producer,
                topic=topic,
                robot_id="robot-phase7-multirun",
                robot_run_id=robot_run_id,
                sequence_number=seq,
                payload_bytes=args.payload_bytes,
            )
    remaining = producer.flush(180.0)
    duration = time.monotonic() - t0
    total = args.num_runs * args.per_run_count
    print(
        json.dumps(
            {
                "mode": "multirun",
                "num_runs": args.num_runs,
                "per_run_count": args.per_run_count,
                "total_messages": total,
                "duration_s": round(duration, 3),
                "msgs_per_s": round(total / duration, 1) if duration > 0 else None,
                "run_ids": run_ids,
                "partitions": {rid: _DELIVERY_PARTITIONS.get(rid) for rid in run_ids},
                "undelivered": remaining,
            }
        )
    )
    return 0 if remaining == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--payload-bytes", type=int, default=64)
    sub = parser.add_subparsers(dest="mode", required=True)

    p_noise = sub.add_parser("noise")
    p_noise.add_argument("--count", type=int, required=True)
    p_noise.add_argument("--num-runs", type=int, default=50)
    p_noise.add_argument("--run-prefix", default="phase7-noise")
    p_noise.set_defaults(func=cmd_noise)

    p_run = sub.add_parser("run")
    p_run.add_argument("--robot-run-id", required=True)
    p_run.add_argument("--robot-id", default="robot-phase7-target")
    p_run.add_argument("--count", type=int, required=True)
    p_run.set_defaults(func=cmd_run)

    p_multi = sub.add_parser("multirun")
    p_multi.add_argument("--num-runs", type=int, required=True)
    p_multi.add_argument("--per-run-count", type=int, required=True)
    p_multi.add_argument("--run-prefix", default="phase7-mr")
    p_multi.set_defaults(func=cmd_multirun)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
