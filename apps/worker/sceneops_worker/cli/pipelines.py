from __future__ import annotations

import typer
from rich import print

from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.execution.dispatcher import create_execution_dispatcher
from sceneops_worker.pipelines.orchestrator import PipelineOrchestrator

app = typer.Typer(
    help="Pipeline orchestration commands.",
    no_args_is_help=True,
)


@app.command("advance")
def advance_pipeline_command(
    pipeline_run_id: str = typer.Option(
        ...,
        "--pipeline-run-id",
        help="Pipeline run ID to advance.",
    ),
) -> None:
    """Take one orchestration step of a QUEUED or RUNNING pipeline run: settle
    its finished Job, then submit the next task's Job to the job backend. The
    step a pipeline worker takes; it never executes a Job itself."""
    print("[bold cyan]SceneOps Worker - Advance pipeline[/bold cyan]")
    print(f"pipeline: {pipeline_run_id}")

    async def _run() -> object:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            dispatcher = create_execution_dispatcher(context.settings.execution)
            return await PipelineOrchestrator(context, dispatcher=dispatcher).advance(
                pipeline_run_id
            )

    pipeline_run = run_cli_async(_run)

    print(f"[bold green]Done.[/bold green] status={pipeline_run.status.value}")
