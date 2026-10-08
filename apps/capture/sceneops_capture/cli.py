"""Capture: durable MCAP capture of one streamed RobotRun.

Runs in the capture image (needs the ROS 2 interface definitions for
schema text). The capture ends, and finalizes, only on the run's explicit
RUN_END control event, which the bridge publishes after its last telemetry
record. ``--idle-timeout-seconds`` is an abort guard, not a finalization
policy: when no new message arrives for that long before RUN_END, the
capture fails (non-zero exit) without finalizing or committing Kafka offsets,
so a bridge that was killed cannot leave a truncated recording that looks
complete. The partial bag stays; re-running the capture discards it and
rebuilds from Kafka's committed offsets.

The finalized bag directory holds the MCAP and a ``capture_receipt.json``
(acquisition metadata, ADR-008) that ``publish --from-capture`` reads, so
publication needs no re-typed arguments. ``--robot-platform`` is the only
optional publication input captured here.

Usage:
    python3 -m sceneops_capture \\
        --robot-id ROBOT --robot-run-id RUN \\
        --output-root /recordings/capture \\
        --channels-file /workspace/channels/surround-camera-lidar.json \\
        --idle-timeout-seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from sceneops_streaming import build_channel_registry
from sceneops_streaming import StreamingSettings

from sceneops_recording.capture.consumer import run_capture


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
    parser = argparse.ArgumentParser(
        prog="python -m sceneops_capture",
        description="Durable MCAP capture from Kafka",
    )
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument(
        "--robot-platform",
        default=None,
        help="Source assertion about the robot platform, recorded in the capture "
        "receipt and carried into the RobotRunManifest on publication.",
    )
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
        "--idle-timeout-seconds",
        type=float,
        help="Abort (without finalizing) after this many seconds with no new "
        "matching message before the run's RUN_END. Default: wait for RUN_END.",
    )
    parser.add_argument("--poll-timeout-seconds", type=float, default=1.0)
    return parser.parse_args(argv)


def _abort_condition(args: argparse.Namespace) -> Callable[[int], bool]:
    if args.idle_timeout_seconds is None:
        return lambda count: False
    return _idle_timeout_stop_condition(args.idle_timeout_seconds)


async def _main_async(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    result = await run_capture(
        settings=settings,
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        output_root=args.output_root,
        stop_condition=_abort_condition(args),
        poll_timeout_seconds=args.poll_timeout_seconds,
        registry=build_channel_registry(args.channels_file),
        stop_on_run_end=True,
        robot_platform=args.robot_platform,
    )

    print("CaptureResult:")
    print(f"  robot_id={result.robot_id}")
    print(f"  robot_run_id={result.robot_run_id}")
    print(f"  path={result.path}")
    print(f"  message_count={result.message_count}")
    print(f"  partition={result.partition}")
    print(f"  first_offset={result.first_offset}  last_offset={result.last_offset}")
    print(
        f"  first_sequence={result.first_sequence}  last_sequence={result.last_sequence}"
    )
    print(f"  sha256={result.sha256}")
    print(f"  receipt_path={result.receipt_path}")
    print(
        "capture_summary "
        + json.dumps(
            {
                "path": str(result.path),
                "receipt_path": (
                    str(result.receipt_path) if result.receipt_path else None
                ),
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
