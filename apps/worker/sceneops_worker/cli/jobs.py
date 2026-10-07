from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

import typer
from rich import print

from sceneops_db.session import async_session_scope, get_async_sessionmaker
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.config import get_settings
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.execution.dispatcher import create_execution_dispatcher
from sceneops_worker.jobs.lease_recovery import (
    DEFAULT_MAX_ACTIONS_PER_PASS,
    recover_expired_leases,
)
from sceneops_worker.jobs.runner import JobRunner

app = typer.Typer(
    help="Job execution commands.",
    no_args_is_help=True,
)


@app.command("run")
def run_job_command(
    job_id: str = typer.Option(..., "--job-id", help="Job ID to execute."),
) -> None:
    print("[bold cyan]SceneOps Worker - Run job[/bold cyan]")
    print(f"job: {job_id}")

    async def _run() -> object:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            dispatcher = create_execution_dispatcher(context.settings.execution)
            return await JobRunner(context, dispatcher=dispatcher).run(job_id)

    job = run_cli_async(_run)

    print(f"[bold green]Done.[/bold green] status={job.status.value}")


@app.command("recover-leases")
def recover_leases_command(
    max_actions: int = typer.Option(
        DEFAULT_MAX_ACTIONS_PER_PASS,
        "--max-actions",
        min=1,
        help="Jobs one pass acts on; the rest wait for the next pass.",
    ),
) -> None:
    """One job lease recovery pass: requeue every RUNNING Job whose lease has
    passed (fail it once its claim budget is spent). Stateless; safe at any
    frequency and concurrently. Prints one JSON summary line."""

    async def _run():
        dispatcher = create_execution_dispatcher(get_settings().execution)
        return await recover_expired_leases(
            session_factory=get_async_sessionmaker(),
            dispatcher=dispatcher,
            max_actions=max_actions,
        )

    actions = run_cli_async(_run)
    summary = {
        "event": "job_lease_recovery_pass",
        "outcomes": dict(Counter(action.outcome.value for action in actions)),
        "actions": [asdict(action) for action in actions],
    }
    typer.echo(json.dumps(summary, default=str, sort_keys=True))
