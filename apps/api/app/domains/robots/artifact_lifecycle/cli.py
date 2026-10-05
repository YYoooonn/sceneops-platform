"""One-shot, read-only artifact lifecycle report (ADR-008 §6, §8 step 12.5).

::

    python -m app.domains.robots.artifact_lifecycle --once \\
        [--capture-report capture_scan.json | --capture-report -] \\
        [--observed-at 2026-10-06T00:00:00Z] \\
        [--pending-grace-seconds N] [--orphan-grace-seconds N] \\
        [--stall-threshold-seconds N] [--verify-recording-bytes]

Reads the ArtifactStore and PostgreSQL the API is configured with, classifies
every object under the RobotRun root and every database reference to one, and
prints the JSON ``ArtifactLifecycleReport``. It writes no object, row, Job or
event and deletes nothing; a class is a classification, not an instruction.

Grace periods are measured against ``--observed-at`` (default: the current UTC
time, recorded in the report). Fixing it makes a report reproducible from
unchanged facts. Grace defaults come from
``SCENEOPS_API_ARTIFACT_LIFECYCLE__PENDING_GRACE_SECONDS`` /
``..._ORPHAN_GRACE_SECONDS``; the stall threshold is the reconciler's.

``--capture-report`` takes the JSON of ``python -m
sceneops_integrations.recording scan-capture``. Without it the capture volume is
unobservable, so a recording without a manifest is protected (PN-3) rather than
an orphan candidate (O1).

``--verify-recording-bytes`` re-reads and hashes the recording of every
registered run; without it registered recordings are checked by listing size
only.

Exit status: 0 when a report was produced (whatever it contains), 1 when the
facts could not be read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta

from app.config import get_settings
from app.domains.robots.reconciliation import (
    ClassificationPolicy,
    postgres_registration_facts,
)
from sceneops_core.robots.registration_failures import REGISTRATION_ATTEMPT_BUDGET
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker
from sceneops_storage import create_artifact_store

from app.domains.robots.cli_support import load_capture_report, parse_observed_at

from .classify import ArtifactLifecyclePolicy
from .service import artifact_lifecycle_once


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.domains.robots.artifact_lifecycle",
        description="Read-only artifact lifecycle classification of the RobotRun root.",
    )
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--capture-report", default=None)
    parser.add_argument(
        "--observed-at",
        default=None,
        help="ISO-8601 timestamp with a UTC offset (default: now).",
    )
    parser.add_argument("--pending-grace-seconds", type=float, default=None)
    parser.add_argument("--orphan-grace-seconds", type=float, default=None)
    parser.add_argument("--stall-threshold-seconds", type=float, default=None)
    parser.add_argument("--verify-recording-bytes", action="store_true")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace, capture_report) -> str:
    settings = get_settings()
    lifecycle = settings.artifact_lifecycle
    stall_seconds = (
        args.stall_threshold_seconds
        if args.stall_threshold_seconds is not None
        else settings.reconciler.stall_threshold_seconds
    )
    if stall_seconds <= 0:
        raise ValueError("the stall threshold must be positive")
    policy = ArtifactLifecyclePolicy(
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
    )
    store = create_artifact_store(settings.artifact)
    try:
        report = await artifact_lifecycle_once(
            artifact_store=store,
            root_uri=settings.artifact.robot_run_root_uri,
            registration_facts=postgres_registration_facts(get_async_sessionmaker()),
            now=parse_observed_at(args.observed_at),
            capture_report=capture_report,
            policy=policy,
            classification_policy=ClassificationPolicy(
                stall_candidate_after=timedelta(seconds=stall_seconds),
                attempt_budget=REGISTRATION_ATTEMPT_BUDGET,
            ),
            verify_recording_bytes=args.verify_recording_bytes,
        )
    finally:
        await dispose_async_engine()
    return json.dumps(report.to_json_dict(), sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output = asyncio.run(_run(args, load_capture_report(args.capture_report)))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(
            f"artifact lifecycle failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 1
    print(output)
    return 0
