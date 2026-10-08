"""One-shot, read-only acquisition status and operational report (ADR-008 §7).

::

    sceneops-worker acquisition status --once \
        [--summary-only] [--capture-report capture_scan.json | --capture-report -] \
        [--observed-at 2026-10-06T00:00:00Z] \
        [--stall-threshold-seconds N] \
        [--pending-grace-seconds N] [--orphan-grace-seconds N] \
        [--verify-recording-bytes]

Reads the ArtifactStore and PostgreSQL the API is configured with and prints the
JSON ``AcquisitionOperationalReport``: the aggregates of ADR-008 §7.2 and, unless
``--summary-only``, one derived ``AcquisitionStatus`` per run. Nothing is stored
and nothing is acted on: it writes no object, row, Job or event, submits no
registration and deletes nothing.

``--observed-at`` fixes the observation time that ages, the stall threshold and
the grace periods are measured against (default: now, recorded in the report);
unchanged facts with a fixed ``--observed-at`` give byte-identical output.
``--capture-report`` takes the JSON of ``python -m sceneops_publisher
scan-capture``; without it the capture stages are unobservable, so a run that is
still capturing or finalized-but-unpublished does not appear at all. The other
options are those of the reconciler and the artifact lifecycle report.

One ``acquisition_status {json}`` record with the aggregates (no per-run
statuses) is also logged to stderr, for log aggregation; stdout stays the report.

Exit status: 0 when a report was produced (whatever it contains), 1 when the
facts could not be read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta

from sceneops_acquisition.lifecycle import ArtifactLifecyclePolicy
from sceneops_acquisition.reconciliation import (
    ClassificationPolicy,
    postgres_registration_facts,
)
from sceneops_recording.capture_scan import CaptureScanReport
from sceneops_recording.recovery_log import (
    STATUS_EVENT,
    configure_cli_logging,
    log_event,
)
from sceneops_acquisition.registration_failures import REGISTRATION_ATTEMPT_BUDGET
from sceneops_acquisition.status.derive import summary_record
from sceneops_acquisition.status.service import acquisition_status_once
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker
from sceneops_storage import create_artifact_store
from sceneops_worker.cli.acquisition.support import (
    load_capture_report,
    parse_observed_at,
)
from sceneops_worker.config import get_settings


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sceneops-worker acquisition status",
        description="Read-only acquisition status and operational report.",
    )
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print the aggregates and the attention list without per-run statuses.",
    )
    parser.add_argument("--capture-report", default=None)
    parser.add_argument(
        "--observed-at",
        default=None,
        help="ISO-8601 timestamp with a UTC offset (default: now).",
    )
    parser.add_argument("--stall-threshold-seconds", type=float, default=None)
    parser.add_argument("--pending-grace-seconds", type=float, default=None)
    parser.add_argument("--orphan-grace-seconds", type=float, default=None)
    parser.add_argument("--verify-recording-bytes", action="store_true")
    return parser.parse_args(argv)


async def _run(
    args: argparse.Namespace, capture_report: CaptureScanReport | None
) -> str:
    settings = get_settings()
    lifecycle = settings.artifact_lifecycle
    stall_seconds = (
        args.stall_threshold_seconds
        if args.stall_threshold_seconds is not None
        else settings.reconciler.stall_threshold_seconds
    )
    if stall_seconds <= 0:
        raise ValueError("the stall threshold must be positive")
    store = create_artifact_store(settings.artifact)
    try:
        report = await acquisition_status_once(
            artifact_store=store,
            root_uri=settings.artifact.robot_run_root_uri,
            registration_facts=postgres_registration_facts(get_async_sessionmaker()),
            now=parse_observed_at(args.observed_at),
            capture_report=capture_report,
            lifecycle_policy=ArtifactLifecyclePolicy(
                pending_grace=timedelta(
                    seconds=args.pending_grace_seconds
                    if args.pending_grace_seconds is not None
                    else lifecycle.pending_grace_seconds
                ),
                orphan_grace=timedelta(
                    seconds=args.orphan_grace_seconds
                    if args.orphan_grace_seconds is not None
                    else lifecycle.orphan_grace_seconds
                ),
            ),
            classification_policy=ClassificationPolicy(
                stall_candidate_after=timedelta(seconds=stall_seconds),
                attempt_budget=REGISTRATION_ATTEMPT_BUDGET,
            ),
            verify_recording_bytes=args.verify_recording_bytes,
            include_runs=not args.summary_only,
        )
    finally:
        await dispose_async_engine()
    log_event(STATUS_EVENT, **summary_record(report))
    return json.dumps(report.to_json_dict(), sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_cli_logging()
    try:
        output = asyncio.run(_run(args, load_capture_report(args.capture_report)))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(
            f"acquisition status failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 1
    print(output)
    return 0
