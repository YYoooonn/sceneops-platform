#!/usr/bin/env python3
"""CLI entry point for durable MCAP capture.

Runs inside the ros2 container (needs rosbag2_py). Externally controls
the capture lifecycle -- exactly one of ``--max-messages`` /
``--idle-timeout-seconds`` decides when to stop, per capture_consumer's
"no built-in notion of done" contract; this module supplies that policy,
run_capture() does not invent one.

Usage:
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
import sys
import time
from collections.abc import Callable
from pathlib import Path

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
    stop_group = parser.add_mutually_exclusive_group(required=True)
    stop_group.add_argument(
        "--max-messages",
        type=int,
        help="Stop once this many messages have been captured (deterministic runs).",
    )
    stop_group.add_argument(
        "--idle-timeout-seconds",
        type=float,
        help="Stop after this many seconds with no new matching message.",
    )
    parser.add_argument("--poll-timeout-seconds", type=float, default=1.0)
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    stop_condition = (
        _max_messages_stop_condition(args.max_messages)
        if args.max_messages is not None
        else _idle_timeout_stop_condition(args.idle_timeout_seconds)
    )

    result = await run_capture(
        settings=settings,
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        output_root=args.output_root,
        stop_condition=stop_condition,
        poll_timeout_seconds=args.poll_timeout_seconds,
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
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
