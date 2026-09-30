#!/usr/bin/env python3
"""Phase 7.0 study: Continuous Capture Router prototype (C).

Runs on the HOST (no rosbag2_py needed -- per the study brief, this
prototype does not need canonical registration, RobotRun creation,
Episode processing, or full recovery/production lifecycle management,
only "consumes telemetry once -> groups/routes by robot_run_id ->
counts or writes minimal per-run sinks"). Real Kafka, one dedicated
fresh consumer group, one subscribe()-based pass from earliest to the
topic's end-of-partition (EOF) watermark captured at start time.

This is Phase 7.0 study tooling (category C evidence-gathering) -- not
the Phase 7.1 production Continuous Capture Router. No MCAP writing, no
canonical state.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from confluent_kafka import Consumer as ConfluentConsumer
from confluent_kafka import TopicPartition

from sceneops_streaming import StreamingSettings
from sceneops_streaming.wire import decode_envelope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=None)
    parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        default=5.0,
        help="stop once every partition has reached its start-of-run "
        "end offset and no new message arrives within this window",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=30.0,
        help="separate, more generous grace period for the FIRST message "
        "(fresh consumer-group join/rebalance can take several seconds "
        "before any message is delivered) -- idle-timeout only starts "
        "counting after the first message arrives",
    )
    parser.add_argument(
        "--dump-runs-file",
        default=None,
        help="optional path to write the full per-run message-count map "
        "(not just the 10-entry sample printed to stdout)",
    )
    args = parser.parse_args()

    settings = StreamingSettings(telemetry_topic=args.topic) if args.topic else StreamingSettings()
    topic = settings.telemetry_topic

    consumer = ConfluentConsumer(
        {
            "bootstrap.servers": settings.bootstrap_servers,
            "group.id": f"phase7-continuous-router-study-{int(time.time())}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])

    # Discover the target end offsets ONCE at start (a single topic pass
    # to "now", matching how a continuous router would drain a fixed
    # snapshot for this benchmark -- in production it would simply keep
    # running, never stopping at an EOF).
    metadata = consumer.list_topics(topic, timeout=10.0)
    partitions = list(metadata.topics[topic].partitions.keys())
    end_offsets: dict[int, int] = {}
    for p in partitions:
        low, high = consumer.get_watermark_offsets(
            TopicPartition(topic, p), timeout=10.0, cached=False
        )
        end_offsets[p] = high

    per_run_counts: dict[str, int] = {}
    per_run_first_seq: dict[str, int] = {}
    per_run_last_seq: dict[str, int] = {}
    per_run_partition: dict[str, int] = {}
    total_messages = 0

    reached: dict[int, bool] = {p: (end_offsets[p] == 0) for p in partitions}

    t0 = time.monotonic()
    last_progress = time.monotonic()
    first_message_seen = False
    while not all(reached.values()):
        msg = consumer.poll(1.0)
        if msg is None:
            grace = args.idle_timeout_seconds if first_message_seen else args.startup_timeout_seconds
            if time.monotonic() - last_progress > grace:
                break
            continue
        if msg.error() is not None:
            raise RuntimeError(f"Kafka consumer error: {msg.error()}")

        first_message_seen = True
        last_progress = time.monotonic()
        envelope = decode_envelope(headers=msg.headers(), value=msg.value())
        total_messages += 1

        rid = envelope.robot_run_id
        per_run_counts[rid] = per_run_counts.get(rid, 0) + 1
        if rid not in per_run_first_seq:
            per_run_first_seq[rid] = envelope.sequence_number
        per_run_last_seq[rid] = envelope.sequence_number
        per_run_partition[rid] = msg.partition()

        if msg.offset() + 1 >= end_offsets[msg.partition()]:
            reached[msg.partition()] = True

    duration = time.monotonic() - t0
    consumer.close()

    print(
        json.dumps(
            {
                "mode": "continuous-router-prototype",
                "topic": topic,
                "partitions": partitions,
                "end_offsets": end_offsets,
                "total_messages_routed": total_messages,
                "distinct_robot_run_ids": len(per_run_counts),
                "duration_s": round(duration, 3),
                "msgs_per_s": round(total_messages / duration, 1) if duration > 0 else None,
                "per_run_counts_sample": dict(list(per_run_counts.items())[:10]),
            }
        )
    )

    if args.dump_runs_file:
        with open(args.dump_runs_file, "w") as f:
            json.dump(
                {
                    "per_run_counts": per_run_counts,
                    "per_run_first_seq": per_run_first_seq,
                    "per_run_last_seq": per_run_last_seq,
                    "per_run_partition": per_run_partition,
                },
                f,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
