from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

import typer

from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.config import get_settings
from sceneops_worker.celery_app import create_execution_dispatcher
from sceneops_execution.executions.recovery import (
    DEFAULT_RESEND_AFTER_SECONDS,
    recover_execution,
)
from sceneops_execution.jobs.lease_recovery import DEFAULT_MAX_ACTIONS_PER_PASS


def recover_command(
    resend_after_seconds: float = typer.Option(
        DEFAULT_RESEND_AFTER_SECONDS,
        "--resend-after-seconds",
        min=0,
        help="How long a QUEUED Job or a waiting PipelineRun may wait on its "
        "message before it is sent again.",
    ),
    max_actions: int = typer.Option(
        DEFAULT_MAX_ACTIONS_PER_PASS,
        "--max-actions",
        min=1,
        help="Resources each sweep acts on; the rest wait for the next pass.",
    ),
) -> None:
    """One execution recovery pass: requeue RUNNING Jobs whose lease passed,
    then re-send the job and advance messages that QUEUED Jobs and waiting
    PipelineRuns have waited on for too long. Stateless; safe at any frequency
    and concurrently. Prints one JSON summary line."""

    async def _run():
        return await recover_execution(
            session_factory=get_async_sessionmaker(),
            dispatcher=create_execution_dispatcher(get_settings().execution),
            resend_after_seconds=resend_after_seconds,
            max_actions=max_actions,
        )

    report = run_cli_async(_run)
    sweeps = {
        "leases": report.leases,
        "dispatches": report.dispatches,
        "advances": report.advances,
    }
    summary = {
        "event": "execution_recovery_pass",
        "outcomes": {
            name: dict(Counter(action.outcome.value for action in actions))
            for name, actions in sweeps.items()
        },
        **{name: [asdict(a) for a in actions] for name, actions in sweeps.items()},
    }
    typer.echo(json.dumps(summary, default=str, sort_keys=True))
