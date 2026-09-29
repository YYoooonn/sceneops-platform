#!/usr/bin/env python3
"""Kafka streaming transport smoke test.

Publishes a deterministic run-A/run-B binary telemetry sequence to the
real local Kafka broker (`make streaming-up`) through the real
``KafkaTelemetryProducer``/``KafkaTelemetryConsumer``, then verifies exact
envelope/payload recovery, per-RobotRun Kafka ordering, the
robot_run_id partition key, and that source_timestamp_ns/
ingest_timestamp_ns each round-trip to their own exact value -- including
the case where they're numerically equal, since they are semantically
distinct clocks, not values required to differ (run-B's fixture below).
See docs/architecture/streaming-transport.md §4.

Uses the real ``StreamingSettings`` configuration path (bootstrap
servers/topic/consumer group base) -- nothing here hardcodes a broker,
topic, or consumer group independently of it; the only smoke-specific
choice is deriving a per-invocation consumer group SUFFIX from the
configured base, for rerunnability.

Leaves zero Postgres/MinIO domain state -- the only side effect is Kafka
topic data on the one configured telemetry topic, bounded by the broker's
own retention, never a SceneOps canonical record.

Every fixture's ``robot_run_id`` is suffixed with a fresh UUID per
invocation so repeated `make smoke-streaming` runs never collide with (or
get confused by) a previous run's leftover topic data -- the consumer
reads from the beginning of the topic and simply ignores any record whose
robot_run_id isn't one this invocation just published.

Usage:
    uv run python scripts/e2e/smoke_streaming.py
    (normally invoked via `make smoke-streaming` -> scripts/e2e/smoke_streaming.sh)
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from dataclasses import dataclass

from sceneops_core.streaming import ConsumedTelemetryEnvelope, EnvelopeEncoding, TelemetryEnvelope
from sceneops_streaming import KafkaTelemetryConsumer, KafkaTelemetryProducer, StreamingSettings

_PASS = 0
_FAIL = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        print(f"  ✅  {label}")
        _PASS += 1
    else:
        suffix = f" -- {detail}" if detail else ""
        print(f"  ❌  {label}{suffix}")
        _FAIL += 1


@dataclass(frozen=True)
class Fixture:
    envelope: TelemetryEnvelope


def _build_fixtures(invocation_id: str) -> tuple[list[Fixture], str, str]:
    run_a_id = f"smoke-run-A-{invocation_id}"
    run_b_id = f"smoke-run-B-{invocation_id}"
    robot_id = f"smoke-robot-{invocation_id}"

    # Deliberately far apart and clearly distinguishable -- proves the
    # envelope never conflates source (robot/sensor) time with ingest
    # (transport-boundary) time.
    base_source_ns = 1_700_000_000_000_000_000
    base_ingest_ns = 1_700_000_005_000_000_000

    common = dict(robot_id=robot_id, message_type="sceneops/smoke/telemetry")

    fixtures = [
        Fixture(
            TelemetryEnvelope(
                **common,
                robot_run_id=run_a_id,
                channel="/a",
                source_timestamp_ns=base_source_ns,
                ingest_timestamp_ns=base_ingest_ns,
                sequence_number=0,
                encoding=EnvelopeEncoding.RAW,
                # 0xFF/0xFE are never valid UTF-8 bytes -- guarantees this
                # payload would raise if anything on the path accidentally
                # decoded it as text.
                payload=bytes([0xFF, 0xFE, 0x00, 0x01, 0xAA]),
            )
        ),
        Fixture(
            TelemetryEnvelope(
                **common,
                robot_run_id=run_a_id,
                channel="/b",
                source_timestamp_ns=base_source_ns + 1_000_000,
                ingest_timestamp_ns=base_ingest_ns + 1_000_000,
                sequence_number=1,
                encoding=EnvelopeEncoding.RAW,
                payload=bytes([0xC0, 0xC1, 0xF5, 0xFF, 0xBB]),
            )
        ),
        Fixture(
            TelemetryEnvelope(
                **common,
                robot_run_id=run_a_id,
                channel="/a",
                source_timestamp_ns=base_source_ns + 2_000_000,
                ingest_timestamp_ns=base_ingest_ns + 2_000_000,
                sequence_number=2,
                encoding=EnvelopeEncoding.RAW,
                payload=bytes([0xED, 0xA0, 0x80, 0xFF, 0xCC]),
            )
        ),
        Fixture(
            TelemetryEnvelope(
                **common,
                robot_run_id=run_b_id,
                channel="/a",
                # Deliberately EQUAL to ingest_timestamp_ns below -- proves
                # against the real broker that source_timestamp_ns ==
                # ingest_timestamp_ns is legal and round-trips exactly.
                # They are semantically distinct clocks, not values
                # required to differ numerically.
                source_timestamp_ns=base_source_ns,
                ingest_timestamp_ns=base_source_ns,
                sequence_number=0,
                encoding=EnvelopeEncoding.RAW,
                payload=bytes([0x80, 0x81, 0xFE, 0xFF, 0xDD]),
            )
        ),
    ]
    return fixtures, run_a_id, run_b_id


async def _publish_all(producer: KafkaTelemetryProducer, fixtures: list[Fixture]) -> None:
    print("--- publishing ---")
    for fx in fixtures:
        await producer.publish(fx.envelope)
        print(
            f"  published robot_run_id={fx.envelope.robot_run_id} "
            f"channel={fx.envelope.channel} seq={fx.envelope.sequence_number}"
        )
    await producer.flush()
    print("  flushed (all records acknowledged by broker)")
    print()


async def _consume_expected(
    consumer: KafkaTelemetryConsumer,
    expected_ids: set[str],
    expected_count: int,
    *,
    timeout_seconds: float,
) -> list[ConsumedTelemetryEnvelope]:
    print("--- consuming ---")
    collected: list[ConsumedTelemetryEnvelope] = []
    deadline = time.monotonic() + timeout_seconds
    while len(collected) < expected_count and time.monotonic() < deadline:
        msg = await consumer.poll(timeout_seconds=1.0)
        if msg is None:
            continue
        if msg.envelope.robot_run_id not in expected_ids:
            continue  # leftover data from a previous smoke-streaming run
        collected.append(msg)
    print(f"  consumed {len(collected)}/{expected_count} matching record(s)")
    print()
    return collected


def _verify(
    fixtures: list[Fixture],
    collected: list[ConsumedTelemetryEnvelope],
    run_a_id: str,
    run_b_id: str,
) -> None:
    print("--- verification ---")
    _check(
        "all records consumed",
        len(collected) == len(fixtures),
        f"got {len(collected)}, expected {len(fixtures)}",
    )

    by_key = {(c.envelope.robot_run_id, c.envelope.sequence_number): c for c in collected}
    for fx in fixtures:
        k = (fx.envelope.robot_run_id, fx.envelope.sequence_number)
        label = f"robot_run_id={fx.envelope.robot_run_id} seq={fx.envelope.sequence_number}"
        consumed = by_key.get(k)
        if consumed is None:
            _check(f"{label}: present", False, "not consumed")
            continue

        _check(f"{label}: payload bytes exact", consumed.envelope.payload == fx.envelope.payload)
        _check(f"{label}: robot_id exact", consumed.envelope.robot_id == fx.envelope.robot_id)
        _check(f"{label}: channel exact", consumed.envelope.channel == fx.envelope.channel)
        _check(
            f"{label}: message_type exact",
            consumed.envelope.message_type == fx.envelope.message_type,
        )
        _check(f"{label}: encoding exact", consumed.envelope.encoding == fx.envelope.encoding)
        _check(
            f"{label}: source_timestamp_ns exact",
            consumed.envelope.source_timestamp_ns == fx.envelope.source_timestamp_ns,
        )
        _check(
            f"{label}: ingest_timestamp_ns exact",
            consumed.envelope.ingest_timestamp_ns == fx.envelope.ingest_timestamp_ns,
        )
        if fx.envelope.source_timestamp_ns == fx.envelope.ingest_timestamp_ns:
            # run-B's fixture deliberately sets these equal -- the wire
            # mapping must preserve that equality, not coerce/reject it.
            _check(
                f"{label}: equal source/ingest timestamps preserved as equal",
                consumed.envelope.source_timestamp_ns == consumed.envelope.ingest_timestamp_ns,
            )
        _check(
            f"{label}: Kafka key == robot_run_id",
            consumed.key == fx.envelope.robot_run_id.encode("utf-8"),
        )

    run_a_records = sorted(
        (c for c in collected if c.envelope.robot_run_id == run_a_id), key=lambda c: c.offset
    )
    run_b_records = [c for c in collected if c.envelope.robot_run_id == run_b_id]

    _check("run-A: all 3 records present", len(run_a_records) == 3, f"got {len(run_a_records)}")
    if len(run_a_records) == 3:
        observed_seq = [c.envelope.sequence_number for c in run_a_records]
        _check(
            "run-A: Kafka order matches publish order (seq 0,1,2)",
            observed_seq == [0, 1, 2],
            f"got {observed_seq}",
        )
        observed_channels = [c.envelope.channel for c in run_a_records]
        _check(
            "run-A: channel order matches publish order (/a,/b,/a)",
            observed_channels == ["/a", "/b", "/a"],
            f"got {observed_channels}",
        )
        partitions = {c.partition for c in run_a_records}
        _check(
            "run-A: all records mapped to exactly one Kafka partition",
            len(partitions) == 1,
            f"got partitions {partitions}",
        )

    _check(
        "run-B: independently consumable (1 record present)",
        len(run_b_records) == 1,
        f"got {len(run_b_records)}",
    )

    if run_a_records and run_b_records:
        run_a_partition = run_a_records[0].partition
        run_b_partition = run_b_records[0].partition
        print(
            f"  (info) run-A partition={run_a_partition}  run-B partition={run_b_partition}"
            "  -- may be same or different, not asserted either way"
        )
    print()


async def main() -> int:
    settings = StreamingSettings()
    invocation_id = uuid.uuid4().hex[:10]
    fixtures, run_a_id, run_b_id = _build_fixtures(invocation_id)

    # Derived from the configured base, not an unrelated literal -- the
    # smoke test's only reason to deviate from settings.consumer_group_id
    # verbatim is per-invocation isolation (so repeated runs don't inherit
    # a previous run's committed offsets); see KafkaTelemetryConsumer's
    # own docstring.
    smoke_group_id = f"{settings.consumer_group_id}-smoke-{invocation_id}"

    print("=== Kafka streaming transport smoke test ===")
    print(f"  bootstrap_servers={settings.bootstrap_servers}")
    print(f"  topic={settings.telemetry_topic}")
    print(f"  consumer_group_id={smoke_group_id}  (base: {settings.consumer_group_id})")
    print(f"  run-A robot_run_id={run_a_id}")
    print(f"  run-B robot_run_id={run_b_id}")
    print()

    producer = KafkaTelemetryProducer(settings=settings)
    await _publish_all(producer, fixtures)
    await producer.close()

    consumer = KafkaTelemetryConsumer(
        settings=settings,
        group_id=smoke_group_id,
        auto_offset_reset="earliest",
    )
    collected = await _consume_expected(
        consumer, {run_a_id, run_b_id}, len(fixtures), timeout_seconds=30.0
    )
    await consumer.close()

    _verify(fixtures, collected, run_a_id, run_b_id)

    print("=" * 60)
    print(f"  Smoke test complete: {_PASS} passed / {_FAIL} failed")
    print("  (zero Postgres/MinIO domain state -- Kafka topic data only)")
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
