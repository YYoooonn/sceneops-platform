#!/usr/bin/env python3
"""Streaming capture scale/backpressure/large-payload benchmark (Phase
6.6, docs/architecture/streaming-reliability-scale-baseline.md).

Runs INSIDE the ros2 container (needs rosbag2_py + real Kafka). Two
phases, run as SEPARATE invocations (so the orchestrating shell script,
scripts/dev/benchmark_streaming_capture_scale.sh, can query real Kafka
consumer-group lag from the HOST in between them -- this container has
no kafka-consumer-groups.sh and no Docker socket to reach the kafka
container's):

    --phase produce   publish N synthetic envelopes at max rate
    --phase capture   durably capture them (ros2/capture.capture_consumer,
                      in-process, not a subprocess) into a real MCAP

No unbounded application-level queue is introduced anywhere in this
benchmark -- publish and capture are separate phases, matching how a
real robot session's Kafka topic already decouples producer and capture
rate: Kafka itself is the backlog buffer; this script only measures how
deep that backlog gets and how fast capture drains it.

Usage: see scripts/dev/benchmark_streaming_capture_scale.sh (the
reproducible entry point -- runs both phases plus the lag checks
between them).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, "/workspace/capture")

from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope  # noqa: E402
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings  # noqa: E402

from capture_consumer import run_capture  # noqa: E402


async def _publish(*, robot_run_id: str, message_count: int, payload_bytes: int) -> float:
    settings = StreamingSettings()
    producer = KafkaTelemetryProducer(settings=settings)
    payload = bytes([0x00, 0x01, 0x00, 0x00]) + (b"x" * max(0, payload_bytes - 4))

    t0 = time.monotonic()
    for i in range(message_count):
        env = TelemetryEnvelope(
            robot_id="robot-scale-bench",
            robot_run_id=robot_run_id,
            channel="/vehicle/odom",
            message_type="nav_msgs/msg/Odometry",
            source_timestamp_ns=1_700_000_000_000_000_000 + i,
            ingest_timestamp_ns=1_700_000_000_500_000_000 + i,
            sequence_number=i,
            encoding=EnvelopeEncoding.ROS2_CDR,
            payload=payload,
        )
        await producer.publish(env)
    await producer.flush(timeout_seconds=180.0)
    await producer.close()
    return time.monotonic() - t0


async def _capture(*, robot_run_id: str, message_count: int, output_root: Path):
    t0 = time.monotonic()
    result = await run_capture(
        settings=StreamingSettings(),
        robot_id="robot-scale-bench",
        robot_run_id=robot_run_id,
        output_root=output_root,
        stop_condition=lambda count: count >= message_count,
        poll_timeout_seconds=2.0,
    )
    return time.monotonic() - t0, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["produce", "capture"], required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--message-count", type=int, required=True)
    parser.add_argument("--payload-bytes", type=int, default=64)
    parser.add_argument("--output-root", default="/data/tmp_scale_bench")
    args = parser.parse_args()

    if args.phase == "produce":
        duration = asyncio.run(
            _publish(
                robot_run_id=args.robot_run_id,
                message_count=args.message_count,
                payload_bytes=args.payload_bytes,
            )
        )
        rate = args.message_count / duration if duration > 0 else float("inf")
        print(json.dumps({
            "phase": "produce",
            "message_count": args.message_count,
            "payload_bytes": args.payload_bytes,
            "duration_s": round(duration, 3),
            "msgs_per_s": round(rate, 1),
        }))
        return 0

    duration, result = asyncio.run(
        _capture(
            robot_run_id=args.robot_run_id,
            message_count=args.message_count,
            output_root=Path(args.output_root),
        )
    )
    rate = args.message_count / duration if duration > 0 else float("inf")
    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # Linux: KB
    print(json.dumps({
        "phase": "capture",
        "message_count": args.message_count,
        "duration_s": round(duration, 3),
        "msgs_per_s": round(rate, 1),
        "mcap_size_bytes": result.path.stat().st_size,
        "peak_rss_kb": peak_rss_kb,
        "sha256": result.sha256,
        "path": str(result.path),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
