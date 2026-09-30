#!/usr/bin/env python3
"""Phase 7.1: real-Kafka multi-run benchmark for ContinuousCaptureRouter.

Runs INSIDE the ros2 container (needs rosbag2_py via mcap_writer.py,
same as every other real capture path). Consumes an already-published
interleaved multi-RobotRun workload (see
scripts/dev/phase7/producer.py's ``multirun`` mode, run from the host
beforehand) in ONE continuous pass, finalizes every run, and reports
timing/throughput/RSS/correctness -- directly comparable to Phase 7.0's
run-scoped multi-run result (`runscoped-batch`,
docs/architecture/streaming-multirun-phase7-study.md §5) and its
continuous-router PROTOTYPE (§7, counting-only, no MCAP writing).

This is Phase 7 study/benchmark tooling (category C evidence-gathering)
-- not a production capture-path change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, "/workspace/capture")

from sceneops_streaming import StreamingSettings  # noqa: E402

from router import ContinuousCaptureRouter  # noqa: E402


async def _run(
    *,
    settings: StreamingSettings,
    output_root: Path,
    total_messages: int,
    max_active_runs: int,
    group_id: str,
    idle_timeout_seconds: float,
) -> dict:
    router = ContinuousCaptureRouter(
        settings=settings,
        output_root=output_root,
        max_active_runs=max_active_runs,
        group_id=group_id,
        poll_timeout_seconds=2.0,
    )
    t0 = time.monotonic()
    try:
        consumed = await router.run_for(
            max_messages=total_messages, idle_timeout_seconds=idle_timeout_seconds
        )
        consume_duration = time.monotonic() - t0
        results = await router.finalize_all()
        total_duration = time.monotonic() - t0
    finally:
        await router.close()

    return {
        "consumed_messages": consumed,
        "consume_duration_s": round(consume_duration, 3),
        "total_duration_s": round(total_duration, 3),
        "consume_msgs_per_s": round(consumed / consume_duration, 1) if consume_duration > 0 else None,
        "finalized_run_count": len(results),
        "failed_run_count": len(router.failed_runs),
        "failed_runs": router.failed_runs,
        "per_run_message_counts": {
            run_id: r.message_count for run_id, r in results.items()
        },
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "stats": {
            "messages_routed": router.stats.messages_routed,
            "messages_written": router.stats.messages_written,
            "messages_skipped_duplicate": router.stats.messages_skipped_duplicate,
            "poison_messages": len(router.stats.poison_messages),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--total-messages", type=int, required=True)
    parser.add_argument("--max-active-runs", type=int, default=256)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--idle-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--output-root", default="/data/tmp_phase7_router_bench")
    args = parser.parse_args()

    settings = StreamingSettings(telemetry_topic=args.topic)
    result = asyncio.run(
        _run(
            settings=settings,
            output_root=Path(args.output_root),
            total_messages=args.total_messages,
            max_active_runs=args.max_active_runs,
            group_id=args.group_id,
            idle_timeout_seconds=args.idle_timeout_seconds,
        )
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
