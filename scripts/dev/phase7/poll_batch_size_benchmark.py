#!/usr/bin/env python3
"""Phase 7.0.1: pick a default ``poll_batch_size`` for
``KafkaTelemetryConsumer`` by measuring real-Kafka throughput at a few
candidate sizes.

Runs on the HOST (only exercises ``sceneops_streaming.KafkaTelemetryConsumer``
directly -- no rosbag2_py/MCAP writing needed to isolate the consumer's
own poll-loop throughput, matching the Phase 7.0 study's own method of
separating consumption cost from writer cost). Real Kafka, real
`KafkaTelemetryConsumer` (the actual class the fix landed in), fresh
run-scoped-style consumer group per candidate so each gets its own full
earliest-to-current-watermark scan of the same, already-populated
topic -- directly comparable to each other and to the Phase 7.0
baseline's own numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time

from confluent_kafka import Consumer as _ProbeConsumer
from confluent_kafka import TopicPartition

from sceneops_streaming import KafkaTelemetryConsumer, StreamingSettings


async def _drain_one_pass(*, settings: StreamingSettings, poll_batch_size: int, end_offset: int) -> dict:
    consumer = KafkaTelemetryConsumer(
        settings=settings,
        group_id=f"phase7-0-1-batchsize-probe-{poll_batch_size}-{int(time.time())}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        poll_batch_size=poll_batch_size,
    )
    count = 0
    t0 = time.monotonic()
    last_progress = time.monotonic()
    first_seen = False
    try:
        while count < end_offset:
            consumed = await consumer.poll(2.0)
            if consumed is None:
                grace = 3.0 if first_seen else 30.0
                if time.monotonic() - last_progress > grace:
                    break
                continue
            first_seen = True
            last_progress = time.monotonic()
            count += 1
    finally:
        await consumer.close()
    duration = time.monotonic() - t0
    return {
        "poll_batch_size": poll_batch_size,
        "messages": count,
        "duration_s": round(duration, 3),
        "msgs_per_s": round(count / duration, 1) if duration > 0 else None,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=None)
    parser.add_argument(
        "--batch-sizes", type=int, nargs="+", default=[32, 128, 512]
    )
    args = parser.parse_args()

    settings = StreamingSettings(telemetry_topic=args.topic) if args.topic else StreamingSettings()
    topic = settings.telemetry_topic

    probe = _ProbeConsumer(
        {"bootstrap.servers": settings.bootstrap_servers, "group.id": "phase7-0-1-watermark-probe"}
    )
    metadata = probe.list_topics(topic, timeout=10.0)
    partitions = list(metadata.topics[topic].partitions.keys())
    assert len(partitions) == 1, "this probe assumes the default single-partition topic"
    _low, end_offset = probe.get_watermark_offsets(
        TopicPartition(topic, partitions[0]), timeout=10.0, cached=False
    )
    probe.close()

    results = []
    for batch_size in args.batch_sizes:
        result = asyncio.run(
            _drain_one_pass(settings=settings, poll_batch_size=batch_size, end_offset=end_offset)
        )
        print(json.dumps(result))
        results.append(result)

    print(json.dumps({"summary": results, "topic_end_offset": end_offset}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
