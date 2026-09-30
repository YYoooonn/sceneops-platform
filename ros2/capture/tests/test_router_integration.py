"""ContinuousCaptureRouter against the REAL local Kafka broker (Phase
7.1) -- no monkeypatched consumer, matching
test_multi_robot_run_integration.py's own convention for the existing
RunScopedCapture path.

Kept deliberately small/fast (a handful of runs, tens of messages) --
this is regression coverage for "the router is correct against a real
broker," run as part of the normal test suite (`make e2e-streaming-
capture`'s stage 1). The larger-scale (100k+/1M message) real-Kafka
benchmark comparison against Phase 7.0's numbers lives separately under
scripts/dev/phase7/ (not part of the fast unit/integration suite).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcap.reader import make_reader  # noqa: E402
from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope  # noqa: E402
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings  # noqa: E402

from router import ROUTER_CONSUMER_GROUP_ID, ContinuousCaptureRouter  # noqa: E402

def _unique_test_topic() -> str:
    """A dedicated, disposable topic per test INVOCATION -- NOT the
    shared default sceneops.robot.telemetry.v1 (local dev/benchmark
    activity, Phase 7.0/7.0.1's own scale studies, has already grown it
    past 1M messages) and NOT even one shared topic across every router
    integration test (a fresh router consumer group always scans from
    `earliest`, so a second test sharing a topic with a first would
    scan the first test's leftover messages before reaching its own --
    exactly the failure this per-invocation uniqueness avoids, the same
    reasoning smoke_streaming.py applies to ITS OWN per-invocation
    consumer-group suffix, just one level up since the router consumes
    by total message count, not by a single target robot_run_id).
    Auto-created on first publish (KAFKA_AUTO_CREATE_TOPICS_ENABLE=true).
    Known minor cleanup item: these accumulate, uncleaned, across
    repeated local/CI runs -- tiny (tens of messages each), same
    accepted-tradeoff class as the default topic's own local-dev growth
    (streaming-reliability-scale-baseline.md's Phase 6.6.1 addendum)."""
    return f"sceneops.robot.telemetry.router-integration-test.{uuid.uuid4().hex[:12]}.v1"


def _envelope(*, robot_run_id: str, sequence_number: int) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        robot_id=f"robot-{robot_run_id}",
        robot_run_id=robot_run_id,
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_700_000_000_000_000_000 + sequence_number,
        ingest_timestamp_ns=1_700_000_000_500_000_000 + sequence_number,
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=bytes([0x00, 0x01, 0x00, 0x00])
        + robot_run_id.encode("ascii").ljust(16, b"\x00"),
    )


def _read_mcap_channel_bytes(path) -> list[bytes]:
    with open(path, "rb") as f:
        reader = make_reader(f)
        return [message.data for _s, _c, message in reader.iter_messages()]


def _unique_group_id() -> str:
    # A fresh group per test invocation -- same reasoning as
    # smoke_streaming.py's own per-invocation suffix: repeated test runs
    # must never inherit a previous run's committed offset.
    return f"{ROUTER_CONSUMER_GROUP_ID}-test-{uuid.uuid4().hex[:10]}"


def test_router_isolates_two_heavily_interleaved_runs_against_real_kafka(tmp_path) -> None:
    invocation = uuid.uuid4().hex[:10]
    run_a = f"router-iso-a-{invocation}"
    run_b = f"router-iso-b-{invocation}"
    topic = _unique_test_topic()

    async def _publish_interleaved():
        settings = StreamingSettings(telemetry_topic=topic)
        producer = KafkaTelemetryProducer(settings=settings)
        # a0,b0,a1,b1,b2,a2,b3 -- A's target (3) is reached while B still
        # has more messages outstanding, on the same real topic/partition.
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=0))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=0))
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=1))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=1))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=2))
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=2))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=3))
        await producer.flush()
        await producer.close()

    asyncio.run(_publish_interleaved())

    async def scenario():
        router = ContinuousCaptureRouter(
            settings=StreamingSettings(telemetry_topic=topic),
            output_root=tmp_path,
            group_id=_unique_group_id(),
            poll_timeout_seconds=2.0,
        )
        try:
            consumed = await router.run_for(max_messages=7, idle_timeout_seconds=10.0)
            assert consumed == 7
            return await router.finalize_all()
        finally:
            await router.close()

    results = asyncio.run(scenario())

    assert results[run_a].message_count == 3
    assert results[run_a].first_sequence == 0
    assert results[run_a].last_sequence == 2
    assert results[run_b].message_count == 4
    assert results[run_b].first_sequence == 0
    assert results[run_b].last_sequence == 3

    payloads_a = _read_mcap_channel_bytes(results[run_a].path)
    payloads_b = _read_mcap_channel_bytes(results[run_b].path)
    assert len(payloads_a) == 3 and len(payloads_b) == 4
    for payload in payloads_a:
        assert run_a.encode("ascii") in payload
        assert run_b.encode("ascii") not in payload
    for payload in payloads_b:
        assert run_b.encode("ascii") in payload
        assert run_a.encode("ascii") not in payload


def test_router_handles_five_interleaved_runs_in_one_continuous_pass(tmp_path) -> None:
    invocation = uuid.uuid4().hex[:10]
    run_ids = [f"router-multi-{i}-{invocation}" for i in range(5)]
    per_run = 6
    topic = _unique_test_topic()

    async def _publish_interleaved():
        settings = StreamingSettings(telemetry_topic=topic)
        producer = KafkaTelemetryProducer(settings=settings)
        for seq in range(per_run):
            for run_id in run_ids:
                await producer.publish(_envelope(robot_run_id=run_id, sequence_number=seq))
        await producer.flush()
        await producer.close()

    asyncio.run(_publish_interleaved())

    async def scenario():
        router = ContinuousCaptureRouter(
            settings=StreamingSettings(telemetry_topic=topic),
            output_root=tmp_path,
            group_id=_unique_group_id(),
            poll_timeout_seconds=2.0,
        )
        try:
            consumed = await router.run_for(
                max_messages=len(run_ids) * per_run, idle_timeout_seconds=10.0
            )
            assert consumed == len(run_ids) * per_run
            return await router.finalize_all()
        finally:
            await router.close()

    results = asyncio.run(scenario())

    assert set(results) == set(run_ids)
    for run_id in run_ids:
        result = results[run_id]
        assert result.message_count == per_run
        assert result.first_sequence == 0
        assert result.last_sequence == per_run - 1
        payloads = _read_mcap_channel_bytes(result.path)
        assert len(payloads) == per_run
        for payload in payloads:
            assert run_id.encode("ascii") in payload
            for other in run_ids:
                if other != run_id:
                    assert other.encode("ascii") not in payload
