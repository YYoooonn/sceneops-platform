"""Kafka audit of one streamed RobotRun: which records the run owns on the
telemetry topic, and how they relate to its capture receipt.

Runs INSIDE the ros2 container (``sceneops_streaming`` + confluent-kafka, the
compose network to the broker). It reads the topic from the beginning with a
throwaway consumer group and commits nothing, so it cannot disturb the capture's
own run-scoped group.

A run's records on its partition are its telemetry envelopes plus two
lifecycle control envelopes on the reserved ``/session/control`` channel, which
the bridge publishes with the run's own key: ``RUN_START`` before the first
telemetry record and ``RUN_END`` after the last. Capture validates them in
their own sequence space and never writes them to the MCAP, so the receipt's
Kafka offset range spans ``message_count + 2`` offsets when the run owns its
partition range alone.

stdin: none. Prints one JSON report; exit 0 only when the run's records are
exactly ``RUN_START``, ``message_count`` telemetry envelopes, ``RUN_END``, in
that order, at the offsets the receipt names.

Usage (in the ros2 container):
    python3 /workspace/scripts/e2e/streaming_kafka_audit.py \
        --robot-run-id RUN --receipt /recordings/capture-X/RUN/capture_receipt.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

from sceneops_core.streaming import RunEventType, is_control_envelope, parse_run_event
from sceneops_streaming import KafkaTelemetryConsumer, StreamingSettings


async def audit(robot_run_id: str, idle_seconds: float) -> list[dict]:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(),
        group_id=f"kafka-audit-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    records: list[dict] = []
    last_progress = time.monotonic()
    try:
        while time.monotonic() - last_progress < idle_seconds:
            consumed = await consumer.poll(1.0)
            if consumed is None:
                continue
            last_progress = time.monotonic()
            envelope = consumed.envelope
            if envelope.robot_run_id != robot_run_id:
                continue
            event = parse_run_event(envelope) if is_control_envelope(envelope) else None
            records.append(
                {
                    "partition": consumed.partition,
                    "offset": consumed.offset,
                    "control": event.value if event else None,
                    "channel": envelope.channel,
                    "sequence": envelope.sequence_number,
                }
            )
            if event is RunEventType.RUN_END:
                break
    finally:
        await consumer.close()
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--idle-seconds", type=float, default=15.0)
    args = parser.parse_args()

    receipt = json.load(open(args.receipt))
    records = asyncio.run(audit(args.robot_run_id, args.idle_seconds))
    control = [r for r in records if r["control"]]
    telemetry = [r for r in records if not r["control"]]
    kafka = receipt["kafka"]
    problems: list[str] = []
    if [r["control"] for r in control] != ["RUN_START", "RUN_END"]:
        problems.append(f"control records are {[r['control'] for r in control]}")
    elif not (records[0] is control[0] and records[-1] is control[1]):
        problems.append("RUN_START is not the first record or RUN_END the last")
    if len(telemetry) != receipt["message_count"]:
        problems.append(
            f"{len(telemetry)} telemetry records, receipt claims {receipt['message_count']}"
        )
    if (
        len({r["partition"] for r in records}) != 1
        or records[0]["partition"] != kafka["partition"]
    ):
        problems.append("the run's records are not on the receipt's one partition")
    if records and (records[0]["offset"], records[-1]["offset"]) != (
        kafka["first_offset"],
        kafka["last_offset"],
    ):
        problems.append("the receipt's offset range is not RUN_START .. RUN_END")
    offsets = [r["offset"] for r in records]
    report = {
        "robot_run_id": args.robot_run_id,
        "partition": kafka["partition"],
        "records": len(records),
        "telemetry_records": len(telemetry),
        "control_records": [
            {k: r[k] for k in ("control", "offset", "sequence", "channel")}
            for r in control
        ],
        "receipt_message_count": receipt["message_count"],
        "receipt_offset_span": kafka["last_offset"] - kafka["first_offset"] + 1,
        # Other runs hashed to the same partition interleave their offsets
        # between this run's records; they never change the run's own count.
        "run_alone_in_offset_span": offsets == list(range(offsets[0], offsets[-1] + 1))
        if offsets
        else False,
        "problems": problems,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
