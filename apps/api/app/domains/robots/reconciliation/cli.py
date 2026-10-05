"""One-shot entrypoint for reconciliation (ADR-008 §3.2): runs without the HTTP
server, keeps no state, prints the report and exits.

::

    python -m app.domains.robots.reconciliation --once \\
        [--apply] [--capture-report capture_scan.json | --capture-report -] \\
        [--stall-threshold-seconds N]

Reads the ArtifactStore and PostgreSQL the API is configured with
(``SCENEOPS_API_ARTIFACT__*``, ``SCENEOPS_DATABASE_URL``), reconciles the
RobotRun root (``ArtifactSettings.robot_run_root_uri``) and prints the JSON
``ReconciliationReport`` on stdout.

Without ``--apply`` (the default) it is read-only: it writes no object, row,
Job or event and submits no registration. With ``--apply`` it additionally
performs the bounded recovery of ``recovery.py`` -- submit, retry or replace a
stalled REGISTER_ROBOT_RUN Job -- through the same Job/dispatch path as
``POST /robot-runs:register``. Classification still needs only the
ArtifactStore and PostgreSQL; only submission touches the broker, and a broker
that is down leaves the committed Job for a later pass.

``--stall-threshold-seconds`` (env ``SCENEOPS_API_RECONCILER__STALL_THRESHOLD_SECONDS``)
is how long a Job may show no activity before it is a stall candidate. It applies
to classification in both modes; its default is derived from measured
registration latency (ADR-008 §5.3).

``--capture-report`` takes the JSON printed by
``python -m sceneops_integrations.recording scan-capture``; the platform never
reads the capture volume itself. Without it, capture states are unobservable and
the report says so (``capture_observed: false``). Recovery never depends on it.

Every recovery action and the end of the pass are also logged to stderr as
``acquisition_recovery {json}`` / ``acquisition_recovery_pass {json}`` records
(``sceneops_core.robots.recovery_log``); stdout stays the one JSON report.

Exit status: 0 when a report was produced (whatever it contains -- individual
action outcomes are in the report), 1 when the facts could not be read.
``--once`` is required: the contract is one complete reconciliation per
invocation; looping belongs to whatever schedules it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta

from app.config import ApiSettings, get_settings
from app.domains.robots.cli_support import load_capture_report
from app.domains.robots.dependencies import get_robot_run_registration_service
from app.platform.executions.backends import CeleryJobExecutionBackend
from app.platform.executions.factory import create_celery_app
from app.platform.jobs.dispatch_facade import JobDispatchFacade
from sceneops_core.robots.capture_scan import CaptureScanReport
from sceneops_core.robots.recovery_log import configure_cli_logging
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker
from sceneops_storage import create_artifact_store

from .recovery import (
    PostgresStalledJobControl,
    RecoveryPolicy,
    reconcile_and_recover,
)
from .service import postgres_registration_facts, reconcile_once

# A broker that accepts connections but never answers must not hang the pass
# (the next one retries); these bound the reconciler process only.
_BROKER_TIMEOUT_SECONDS = 5


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.domains.robots.reconciliation",
        description="Acquisition reconciliation: classify every run; --apply recovers.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="Run one complete reconciliation and exit.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform bounded registration recovery (default: observe only).",
    )
    parser.add_argument(
        "--stall-threshold-seconds",
        type=float,
        default=None,
        help="Inactivity after which a registration Job is a stall candidate "
        "(default: SCENEOPS_API_RECONCILER__STALL_THRESHOLD_SECONDS).",
    )
    parser.add_argument(
        "--capture-report",
        default=None,
        help="JSON from `scan-capture` ('-' reads stdin); optional.",
    )
    return parser.parse_args(argv)


def _stall_threshold(settings: ApiSettings, override: float | None) -> timedelta:
    seconds = (
        override
        if override is not None
        else settings.reconciler.stall_threshold_seconds
    )
    if seconds <= 0:
        raise ValueError("the stall threshold must be positive")
    return timedelta(seconds=seconds)


def build_registration_submitter(settings: ApiSettings):
    """The API's own submission path (Job + dispatch), built without FastAPI.
    Creating the Celery app opens no connection."""
    celery_cfg = settings.execution.celery
    celery_app = create_celery_app(
        broker_url=celery_cfg.broker_url, result_backend=celery_cfg.result_backend
    )
    celery_app.conf.broker_connection_timeout = _BROKER_TIMEOUT_SECONDS
    celery_app.conf.broker_transport_options = {
        "socket_connect_timeout": _BROKER_TIMEOUT_SECONDS,
        "socket_timeout": _BROKER_TIMEOUT_SECONDS,
    }
    celery_app.conf.result_backend_transport_options = {
        "socket_connect_timeout": _BROKER_TIMEOUT_SECONDS,
        "socket_timeout": _BROKER_TIMEOUT_SECONDS,
    }
    facade = JobDispatchFacade(
        session_factory=get_async_sessionmaker(),
        job_backend=CeleryJobExecutionBackend(
            app=celery_app, job_queue=celery_cfg.job_queue
        ),
    )
    return get_robot_run_registration_service(settings, facade)


async def _run(
    capture_report: CaptureScanReport | None,
    *,
    apply: bool,
    stall_threshold_seconds: float | None,
) -> str:
    settings = get_settings()
    stall_threshold = _stall_threshold(settings, stall_threshold_seconds)
    store = create_artifact_store(settings.artifact)
    facts = postgres_registration_facts(get_async_sessionmaker())
    try:
        if apply:
            report = await reconcile_and_recover(
                artifact_store=store,
                root_uri=settings.artifact.robot_run_root_uri,
                registration_facts=facts,
                submitter=build_registration_submitter(settings),
                jobs=PostgresStalledJobControl(get_async_sessionmaker()),
                policy=RecoveryPolicy(stall_threshold=stall_threshold),
                capture_report=capture_report,
            )
        else:
            report = await reconcile_once(
                artifact_store=store,
                root_uri=settings.artifact.robot_run_root_uri,
                registration_facts=facts,
                capture_report=capture_report,
                policy=RecoveryPolicy(stall_threshold=stall_threshold).classification(),
            )
    finally:
        await dispose_async_engine()
    return json.dumps(report.to_json_dict(), sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_cli_logging()
    try:
        output = asyncio.run(
            _run(
                load_capture_report(args.capture_report),
                apply=args.apply,
                stall_threshold_seconds=args.stall_threshold_seconds,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"reconcile failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0
