"""Capture: durable MCAP capture of one streamed RobotRun.

Runs in the capture image (needs the ROS 2 interface definitions for
schema text). The capture ends, and finalizes, only on the run's explicit
RUN_END control event, which the bridge publishes after its last telemetry
record, and only for a complete RUN_START, telemetry, RUN_END session of that
run (``sceneops_recording.capture.lifecycle``). ``--idle-timeout-seconds`` is an
abort guard, not a finalization policy: when the run shows no activity for that
long before RUN_END, the capture fails (non-zero exit) without finalizing or
committing Kafka offsets, so a bridge that was killed cannot leave a truncated
recording that looks complete. The partial bag stays; re-running the capture
discards it and rebuilds the run from the start position (the beginning of the
topic when none is given). Only one capture of a run can run at a time on an output
root; a second one fails at once.

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
from pathlib import Path

from sceneops_streaming import (
    KafkaStartPosition,
    StreamingSettings,
    build_channel_registry,
)

from sceneops_recording.capture.consumer import run_capture


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
        help="Abort (without finalizing) after this many seconds without progress "
        "before the run's RUN_END: before RUN_START, no record of any run "
        "arrives (without --start-position this covers the consumer group join, "
        "~3 s); after it, no record of this run arrives, whatever other runs "
        "publish. Default: wait for RUN_END.",
    )
    parser.add_argument(
        "--start-position",
        type=_start_position,
        metavar="TOPIC:PARTITION:OFFSET",
        help="Read exactly this partition from this offset (normally the offset of "
        "the run's RUN_START) instead of scanning the whole topic from the "
        "beginning: no consumer-group join, no rebalance. A position that is not "
        "in the retained log, or that does not lead to the run's RUN_START, fails "
        "the capture; it is never replaced by another position.",
    )
    parser.add_argument("--poll-timeout-seconds", type=float, default=1.0)
    return parser.parse_args(argv)


def _start_position(text: str) -> KafkaStartPosition:
    try:
        topic, partition, offset = text.rsplit(":", 2)
        return KafkaStartPosition(topic, int(partition), int(offset))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected TOPIC:PARTITION:OFFSET, got {text!r}: {exc}"
        ) from exc


async def _main_async(args: argparse.Namespace) -> int:
    settings = StreamingSettings()
    result = await run_capture(
        settings=settings,
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        output_root=args.output_root,
        idle_timeout_seconds=args.idle_timeout_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
        registry=build_channel_registry(args.channels_file),
        stop_on_run_end=True,
        robot_platform=args.robot_platform,
        start=args.start_position,
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
