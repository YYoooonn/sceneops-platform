#!/usr/bin/env python3
"""CLI entry point for durable MCAP capture.

Runs inside the ros2 container (needs the ROS 2 interface definitions for
schema text). Externally controls the capture lifecycle: this module
supplies the stop policy, run_capture() does not invent one. At least one
of ``--until-run-end`` (the bridge's explicit RUN_END control event, the
normal end of a streamed run), ``--max-messages`` and
``--idle-timeout-seconds`` is required; the first one met ends the capture.
An idle timeout finalizes what was captured when no RUN_END ever arrives (a
bridge that was killed); it cannot tell a complete run from a truncated one.

Usage:
    python3 /workspace/capture/cli.py \\
        --robot-id ROBOT --robot-run-id RUN \\
        --output-root /recordings/capture \\
        --channels-file /workspace/channels/surround-camera-lidar.json \\
        --until-run-end --idle-timeout-seconds 60
    python3 /workspace/capture/cli.py \\
        --robot-id ROBOT --robot-run-id RUN \\
        --output-root /data/captured/mcap \\
        --max-messages 2915
    python3 /workspace/capture/cli.py \\
        --robot-id ROBOT --robot-run-id RUN \\
        --output-root /data/captured/mcap \\
        --idle-timeout-seconds 10.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from sceneops_core.streaming import build_channel_registry
from sceneops_streaming import StreamingSettings

from capture_consumer import run_capture


def _max_messages_stop_condition(max_messages: int) -> Callable[[int], bool]:
    return lambda count: count >= max_messages


def _idle_timeout_stop_condition(idle_timeout_seconds: float) -> Callable[[int], bool]:
    state = {"last_count": -1, "last_progress": time.monotonic()}

    def stop_condition(count: int) -> bool:
        now = time.monotonic()
        if count != state["last_count"]:
            state["last_count"] = count
            state["last_progress"] = now
            return False
        return (now - state["last_progress"]) >= idle_timeout_seconds

    return stop_condition


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Durable MCAP capture from Kafka")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--channels-file",
        type=Path,
        action="append",
        default=[],
        help="Channel-set JSON file adding channels to the defaults (repeatable); "
        "must match the bridge's",
    )
    parser.add_argument(
        "--until-run-end",
        action="store_true",
        help="Stop when the run's explicit RUN_END control event is consumed.",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="Stop once this many messages have been captured (deterministic runs).",
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        help="Stop after this many seconds with no new matching message.",
    )
    parser.add_argument("--poll-timeout-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not (args.until_run_end or args.max_messages or args.idle_timeout_seconds):
        parser.error(
            "one of --until-run-end, --max-messages, --idle-timeout-seconds is required"
        )
    return args


def _stop_condition(args: argparse.Namespace) -> Callable[[int], bool]:
    conditions: list[Callable[[int], bool]] = []
    if args.max_messages is not None:
        conditions.append(_max_messages_stop_condition(args.max_messages))
    if args.idle_timeout_seconds is not None:
        conditions.append(_idle_timeout_stop_condition(args.idle_timeout_seconds))
    # With only --until-run-end the stop condition never fires by itself.
    return lambda count: any(condition(count) for condition in conditions)


async def _main_async(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    result = await run_capture(
        settings=settings,
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        output_root=args.output_root,
        stop_condition=_stop_condition(args),
        poll_timeout_seconds=args.poll_timeout_seconds,
        registry=build_channel_registry(args.channels_file),
        stop_on_run_end=args.until_run_end,
    )

    print("CaptureResult:")
    print(f"  robot_id={result.robot_id}")
    print(f"  robot_run_id={result.robot_run_id}")
    print(f"  path={result.path}")
    print(f"  message_count={result.message_count}")
    print(f"  partition={result.partition}")
    print(f"  first_offset={result.first_offset}  last_offset={result.last_offset}")
    print(f"  first_sequence={result.first_sequence}  last_sequence={result.last_sequence}")
    print(f"  sha256={result.sha256}")
    print(
        "capture_summary "
        + json.dumps(
            {
                "path": str(result.path),
                "message_count": result.message_count,
                "sha256": result.sha256,
                "per_channel_counts": dict(sorted(result.per_channel_counts.items())),
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
