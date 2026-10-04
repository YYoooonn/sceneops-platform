#!/usr/bin/env python3
"""Durable MCAP capture E2E verification.

Runs on the HOST via `uv run` (RosbagAdapter needs no rclpy/ROS2 install
-- it decodes CDR bytes using the schema text embedded in the MCAP file
itself, via mcap-ros2-support). Reads the Kafka-captured MCAP through the
same RosbagAdapter apps/worker uses for real ingestion (mandatory
compatibility check -- no DB writes, no Episode/RobotRun creation, just
`extract_robot_states()` / `extract_missions()`), and compares it against a directly
`ros2 bag record`-produced MCAP of the same scene for SEMANTIC
equivalence (topic/schema set, robot_state/mission counts) -- not
byte-for-byte identity, which isn't expected (different writer
invocations, different wall-clock write times in metadata.yaml).

Usage (normally invoked by scripts/e2e/e2e_streaming_capture.sh):
    uv run python scripts/e2e/mcap_capture_verify.py \\
        --captured-mcap /path/to/kafka-captured/run_0.mcap \\
        --direct-mcap /path/to/direct-recorded/scene_0.mcap \\
        --robot-id ROBOT --robot-run-id RUN \\
        --expected-message-count 2915 \\
        --expected-first-sequence 0
"""

from __future__ import annotations

from types import SimpleNamespace

import argparse
import sys
from unittest.mock import MagicMock

from mcap.reader import make_reader

from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter

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


def _channel_schema_set(mcap_path: str) -> set[tuple[str, str]]:
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        return {
            (channel.topic, schema.name if schema is not None else "")
            for schema, channel, _message in reader.iter_messages()
        }


def _message_count(mcap_path: str) -> int:
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        return sum(1 for _ in reader.iter_messages())


def main() -> int:
    parser = argparse.ArgumentParser(description="Durable MCAP capture E2E verification")
    parser.add_argument("--captured-mcap", required=True)
    parser.add_argument("--direct-mcap", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--expected-message-count", type=int, required=True)
    args = parser.parse_args()

    print("=== MCAP capture E2E verification ===")
    print(f"  captured (Kafka path): {args.captured_mcap}")
    print(f"  direct   (ros2 bag record path): {args.direct_mcap}")
    print()

    print("--- message counts ---")
    captured_count = _message_count(args.captured_mcap)
    direct_count = _message_count(args.direct_mcap)
    _check(
        "captured message count matches bridge-published count",
        captured_count == args.expected_message_count,
        f"got {captured_count}, expected {args.expected_message_count}",
    )
    print(f"  (info) direct-record message count: {direct_count}")
    print()

    print("--- topic/schema semantic equivalence (not byte-identical) ---")
    captured_channels = _channel_schema_set(args.captured_mcap)
    direct_channels = _channel_schema_set(args.direct_mcap)
    _check(
        "captured and direct-recorded bags expose the same topic/schema set",
        captured_channels == direct_channels,
        f"captured={captured_channels} direct={direct_channels}",
    )
    print()

    print("--- RosbagAdapter compatibility (mandatory; no DB writes) ---")
    captured_adapter = RosbagAdapter(
        source_store=MagicMock(), source_root_uri=args.captured_mcap
    )
    direct_adapter = RosbagAdapter(source_store=MagicMock(), source_root_uri=args.direct_mcap)

    captured_source = SimpleNamespace(
        robot_states=captured_adapter.extract_robot_states(
            robot_id=args.robot_id, robot_run_id=args.robot_run_id
        ),
        missions=captured_adapter.extract_missions(
            robot_id=args.robot_id, robot_run_id=args.robot_run_id
        ),
    )
    direct_source = SimpleNamespace(
        robot_states=direct_adapter.extract_robot_states(
            robot_id=args.robot_id, robot_run_id=args.robot_run_id
        ),
        missions=direct_adapter.extract_missions(
            robot_id=args.robot_id, robot_run_id=args.robot_run_id
        ),
    )

    _check(
        "RosbagAdapter opens the captured MCAP without error",
        captured_source is not None,
    )
    _check(
        "captured bag: robot_states non-empty",
        len(captured_source.robot_states) > 0,
        f"got {len(captured_source.robot_states)}",
    )
    _check(
        "captured bag: missions non-empty",
        len(captured_source.missions) > 0,
        f"got {len(captured_source.missions)}",
    )
    # A tolerance, not exact equality: `captured` and `direct` come from
    # two INDEPENDENT CAN replay invocations, each subject to its own
    # ROS2 DDS discovery-lag at startup (a handful of early messages can
    # be missed before subscribers finish connecting -- see
    # docs/architecture/streaming-transport.md), and RosbagAdapter
    # dedupes RobotState records by microsecond timestamp, which can
    # collapse a couple of same-microsecond messages either way. Exact
    # equality between two separately-timed replay runs is not the right
    # oracle here; semantic equivalence (same order of magnitude, same
    # topic/schema set, same mission identity) is.
    robot_state_delta = abs(len(captured_source.robot_states) - len(direct_source.robot_states))
    robot_state_tolerance = max(10, round(0.02 * len(direct_source.robot_states)))
    _check(
        "robot_state count is within tolerance between captured and "
        "direct-recorded bags (two independent replay runs)",
        robot_state_delta <= robot_state_tolerance,
        f"captured={len(captured_source.robot_states)} "
        f"direct={len(direct_source.robot_states)} "
        f"delta={robot_state_delta} tolerance={robot_state_tolerance}",
    )
    _check(
        "mission count matches between captured and direct-recorded bags",
        len(captured_source.missions) == len(direct_source.missions),
        f"captured={len(captured_source.missions)} direct={len(direct_source.missions)}",
    )

    captured_mission_ids = {m.mission_id for m in captured_source.missions}
    direct_mission_ids = {m.mission_id for m in direct_source.missions}
    _check(
        "mission_ids match between captured and direct-recorded bags",
        captured_mission_ids == direct_mission_ids,
        f"captured={captured_mission_ids} direct={direct_mission_ids}",
    )
    print()

    print("=" * 60)
    print(f"  MCAP capture E2E verification complete: {_PASS} passed / {_FAIL} failed")
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
