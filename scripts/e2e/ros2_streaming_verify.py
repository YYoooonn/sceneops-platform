#!/usr/bin/env python3
"""ROS2 streaming bridge E2E verification.

Runs INSIDE the ros2 container (mounted read-only at
/workspace/scripts, compose/ros2.yaml) -- needs real rclpy
message classes + deserialize_message to prove "payload bytes decode as
the declared ROS2 message type" for every channel, not just the
String-based ones, and reaches Kafka at kafka:9092 directly (in-network,
no host-port override needed here).

Consumes everything published under one robot_run_id from the real local
Kafka broker (via the real KafkaTelemetryConsumer/StreamingSettings --
the same Kafka transport `make smoke-streaming` exercises) and checks it
against what the bridge itself reported publishing. See
docs/architecture/streaming-transport.md's ROS2 streaming bridge section.

Usage (normally invoked by scripts/e2e/e2e_ros2_streaming.sh):
    python3 scripts/e2e/ros2_streaming_verify.py \\
        --robot-id robot-nuscenes-streaming --robot-run-id run-... \\
        --expected-count 2888
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from nav_msgs.msg import Odometry
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import String

from sceneops_core.streaming import ConsumedTelemetryEnvelope, EnvelopeEncoding
from sceneops_streaming import KafkaTelemetryConsumer, StreamingSettings

_PASS = 0
_FAIL = 0

# Single source of truth for this script's expectations -- deliberately a
# SEPARATE, independent map from ros2/nodes/streaming_bridge_node.py's
# TOPIC_SPECS (not imported from it), so this verification proves the
# real wire content matches an independently-stated expectation rather
# than trivially agreeing with whatever the bridge's own module defines.
_EXPECTED_CHANNELS: dict[str, tuple[type, str]] = {
    "/vehicle/odom": (Odometry, "nav_msgs/msg/Odometry"),
    "/vehicle/imu": (Imu, "sensor_msgs/msg/Imu"),
    "/vehicle/status": (BatteryState, "sensor_msgs/msg/BatteryState"),
    "/vehicle/control": (String, "std_msgs/msg/String"),
    "/mission/status": (String, "std_msgs/msg/String"),
}

# Which CAN source file backs each real-observation channel, and the CAN
# 'utime' unit conversion (microseconds -> ns, matching
# ros2/nodes/can_timestamp.py -- reimplemented here independently rather
# than imported, so this provenance check proves agreement with the raw
# source data, not just internal self-consistency with the bridge's own
# helper). /mission/status is deliberately excluded -- it has no CAN
# source file, by design (it is a synthetic replay-boundary event).
_CAN_SOURCE_FILE_BY_CHANNEL: dict[str, str] = {
    "/vehicle/odom": "pose",
    "/vehicle/imu": "ms_imu",
    "/vehicle/status": "vehicle_monitor",
    "/vehicle/control": "vehicle_monitor",
}


def _load_can_source_timestamps_ns(dataroot: str, scene: str) -> dict[str, set[int]]:
    """Read the real nuScenes CAN bus fixture files directly (the same
    ones can_replay_node.py reads) and return, per source table, the
    exact set of source_timestamp_ns values a faithful bridge could ever
    legitimately produce. Provenance-equality checks assert every
    consumed envelope's timestamp is a MEMBER of this set -- not merely
    non-zero."""

    can_bus_dir = Path(dataroot) / "can_bus"
    timestamps_by_table: dict[str, set[int]] = {}
    for table in ("pose", "ms_imu", "vehicle_monitor"):
        path = can_bus_dir / f"{scene}_{table}.json"
        with path.open() as f:
            records = json.load(f)
        timestamps_by_table[table] = {int(r["utime"]) * 1_000 for r in records}
    return timestamps_by_table


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        print(f"  ✅  {label}")
        _PASS += 1
    else:
        suffix = f" -- {detail}" if detail else ""
        print(f"  ❌  {label}{suffix}")
        _FAIL += 1


async def _consume_all(
    settings: StreamingSettings,
    robot_run_id: str,
    expected_count: int,
    timeout_seconds: float,
) -> list[ConsumedTelemetryEnvelope]:
    consumer = KafkaTelemetryConsumer(
        settings=settings,
        group_id=f"{settings.consumer_group_id}-e2e-ros2-{robot_run_id}",
        auto_offset_reset="earliest",
    )
    collected: list[ConsumedTelemetryEnvelope] = []
    deadline = time.monotonic() + timeout_seconds
    while len(collected) < expected_count and time.monotonic() < deadline:
        msg = await consumer.poll(timeout_seconds=1.0)
        if msg is None:
            continue
        if msg.envelope.robot_run_id != robot_run_id:
            continue  # leftover data from a previous run
        collected.append(msg)
    await consumer.close()
    return collected


def _verify(
    collected: list[ConsumedTelemetryEnvelope],
    *,
    robot_id: str,
    robot_run_id: str,
    expected_count: int,
    can_source_timestamps_ns: dict[str, set[int]],
) -> None:
    _check(
        "all published messages consumed",
        len(collected) == expected_count,
        f"got {len(collected)}, expected {expected_count}",
    )

    _check(
        "robot_id exact for every consumed message",
        all(c.envelope.robot_id == robot_id for c in collected),
    )
    _check(
        "encoding == ros2-cdr for every consumed message",
        all(c.envelope.encoding == EnvelopeEncoding.ROS2_CDR for c in collected),
    )
    _check(
        "Kafka key == robot_run_id for every consumed message",
        all(c.key == robot_run_id.encode("utf-8") for c in collected),
    )
    _check(
        "every source_timestamp_ns is populated (> 0)",
        all(c.envelope.source_timestamp_ns > 0 for c in collected),
    )

    channels_seen = {c.envelope.channel for c in collected}
    _check(
        "all 5 expected channels observed",
        channels_seen == set(_EXPECTED_CHANNELS),
        f"got {channels_seen}",
    )

    by_channel: dict[str, list[ConsumedTelemetryEnvelope]] = {}
    for c in collected:
        by_channel.setdefault(c.envelope.channel, []).append(c)

    for channel, (msg_cls, expected_type_name) in _EXPECTED_CHANNELS.items():
        records = by_channel.get(channel, [])
        _check(f"{channel}: at least one message observed", len(records) > 0)

        type_ok = all(r.envelope.message_type == expected_type_name for r in records)
        _check(f"{channel}: message_type == {expected_type_name}", type_ok)

        decode_ok = True
        for r in records:
            try:
                deserialize_message(r.envelope.payload, msg_cls)
            except Exception:
                decode_ok = False
                break
        _check(
            f"{channel}: payload decodes as {expected_type_name} for every message "
            f"(real rclpy.deserialize_message, {len(records)} message(s))",
            decode_ok,
        )

    mission_records = sorted(by_channel.get("/mission/status", []), key=lambda r: r.offset)
    _check(
        "/mission/status: exactly 2 messages (start+end boundary)",
        len(mission_records) == 2,
        f"got {len(mission_records)}",
    )
    if len(mission_records) == 2:
        states = [
            deserialize_message(r.envelope.payload, String).data for r in mission_records
        ]
        _check(
            "/mission/status: first message is 'running'",
            '"operation_state": "running"' in states[0],
            states[0],
        )
        _check(
            "/mission/status: second message is 'completed'",
            '"operation_state": "completed"' in states[1],
            states[1],
        )

    status_records = by_channel.get("/vehicle/status", [])
    control_records = by_channel.get("/vehicle/control", [])
    _check(
        "/vehicle/status count == /vehicle/control count (1:1 from vehicle_monitor CAN entries)",
        len(status_records) == len(control_records),
        f"{len(status_records)} vs {len(control_records)}",
    )
    if control_records:
        sample = deserialize_message(control_records[0].envelope.payload, String)
        _check(
            "/vehicle/control: observed vehicle feedback JSON (steering/throttle/brake), "
            "never renamed to action/command/policy_output",
            all(k in sample.data for k in ("steering", "throttle", "brake")),
            sample.data,
        )

    # Provenance equality: every real-observation channel's consumed
    # source_timestamp_ns must be a MEMBER of the exact CAN source
    # timestamp set read directly from the raw fixture files -- not just
    # non-zero, not just "populated". /mission/status is excluded on
    # purpose (no CAN source exists for it -- it is a synthetic
    # replay-boundary event).
    for channel, table in _CAN_SOURCE_FILE_BY_CHANNEL.items():
        records = by_channel.get(channel, [])
        expected_ns = can_source_timestamps_ns[table]
        mismatched = [
            r.envelope.source_timestamp_ns
            for r in records
            if r.envelope.source_timestamp_ns not in expected_ns
        ]
        _check(
            f"{channel}: source_timestamp_ns is a real '{table}' CAN record utime "
            f"for every message ({len(records)} checked against {len(expected_ns)} "
            "known source timestamps)",
            not mismatched,
            f"{len(mismatched)} value(s) not found in the source data, e.g. {mismatched[:3]}",
        )

    mission_status_ns = {r.envelope.source_timestamp_ns for r in mission_records}
    all_can_ns = set().union(*can_source_timestamps_ns.values())
    _check(
        "/mission/status: source_timestamp_ns is NOT a CAN observation "
        "(synthetic replay-event time) -- distinct from every real CAN "
        "source timestamp in this scene",
        mission_status_ns.isdisjoint(all_can_ns),
        f"unexpected overlap: {mission_status_ns & all_can_ns}",
    )

    partitions = {c.partition for c in collected}
    _check(
        "all RobotRun records land in exactly one Kafka partition",
        len(partitions) == 1,
        f"got partitions {partitions}",
    )

    ordered = sorted(collected, key=lambda c: c.offset)
    seqs = [c.envelope.sequence_number for c in ordered]
    _check(
        "Kafka consumption order matches bridge publication order "
        "(sequence_number strictly increasing when sorted by Kafka offset)",
        seqs == sorted(seqs) and len(set(seqs)) == len(seqs),
        f"first 3: {seqs[:3]}, last 3: {seqs[-3:]}",
    )
    _check(
        "sequence_number covers 0..N-1 with no gaps (one counter across all channels)",
        seqs == list(range(len(seqs))),
        f"expected 0..{len(seqs) - 1}",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="ROS2 streaming bridge E2E verification")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--scene", default="scene-0061")
    parser.add_argument("--dataroot", default="/data/raw/nuscenes")
    args = parser.parse_args()

    settings = StreamingSettings()

    print("=== ROS2 streaming bridge E2E verification ===")
    print(f"  robot_id={args.robot_id}")
    print(f"  robot_run_id={args.robot_run_id}")
    print(f"  expected_count={args.expected_count}")
    print(f"  scene={args.scene}  dataroot={args.dataroot}")
    print(f"  bootstrap_servers={settings.bootstrap_servers}  topic={settings.telemetry_topic}")
    print()

    can_source_timestamps_ns = _load_can_source_timestamps_ns(args.dataroot, args.scene)
    print(
        "  loaded real CAN source timestamps: "
        + ", ".join(f"{k}={len(v)}" for k, v in can_source_timestamps_ns.items())
    )
    print()

    collected = await _consume_all(
        settings, args.robot_run_id, args.expected_count, args.timeout_seconds
    )
    print(f"  consumed {len(collected)} matching record(s)")
    print()

    print("--- verification ---")
    _verify(
        collected,
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        expected_count=args.expected_count,
        can_source_timestamps_ns=can_source_timestamps_ns,
    )

    print()
    print("=" * 60)
    print(f"  E2E verification complete: {_PASS} passed / {_FAIL} failed")
    print("  (zero Postgres/MinIO domain state -- Kafka topic data only)")
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
