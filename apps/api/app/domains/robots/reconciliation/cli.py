"""One-shot entrypoint for reconciliation (ADR-008 §3.2): runs without the HTTP
server, keeps no state, prints the report and exits.

::

    python -m app.domains.robots.reconciliation --once \\
        [--capture-report capture_scan.json | --capture-report -]

Reads the ArtifactStore and PostgreSQL the API is configured with
(``SCENEOPS_API_ARTIFACT__*``, ``SCENEOPS_DATABASE_URL``), reconciles the
RobotRun root (``ArtifactSettings.robot_run_root_uri``) and prints the JSON
``ReconciliationReport`` on stdout. It is read-only: it writes no object, row,
Job or event, and submits no registration.

``--capture-report`` takes the JSON printed by
``python -m sceneops_integrations.recording scan-capture``; the platform never
reads the capture volume itself. Without it, capture states are unobservable and
the report says so (``capture_observed: false``).

Exit status: 0 when a report was produced (whatever it contains -- acting on a
state is not this command's job), 1 when the facts could not be read.
``--once`` is required: the contract is one complete reconciliation per
invocation; looping belongs to whatever schedules it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings
from sceneops_core.robots.capture_scan import CaptureScanReport
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker
from sceneops_storage import create_artifact_store

from .service import postgres_registration_facts, reconcile_once


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.domains.robots.reconciliation",
        description="Read-only acquisition reconciliation: classify every run.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="Run one complete reconciliation and exit.",
    )
    parser.add_argument(
        "--capture-report",
        default=None,
        help="JSON from `scan-capture` ('-' reads stdin); optional.",
    )
    return parser.parse_args(argv)


def _load_capture_report(source: str | None) -> CaptureScanReport | None:
    if source is None:
        return None
    raw = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    return CaptureScanReport.model_validate_json(raw)


async def _run(capture_report: CaptureScanReport | None) -> str:
    settings = get_settings()
    try:
        report = await reconcile_once(
            artifact_store=create_artifact_store(settings.artifact),
            root_uri=settings.artifact.robot_run_root_uri,
            registration_facts=postgres_registration_facts(get_async_sessionmaker()),
            capture_report=capture_report,
        )
    finally:
        await dispose_async_engine()
    return json.dumps(report.to_json_dict(), sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output = asyncio.run(_run(_load_capture_report(args.capture_report)))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"reconcile failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0
