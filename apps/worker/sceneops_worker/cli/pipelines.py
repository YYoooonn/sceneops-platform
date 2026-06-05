from __future__ import annotations

import typer
from rich import print

from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.pipelines.runner import PipelineRunner

app = typer.Typer(
    help="Pipeline execution commands.",
    no_args_is_help=True,
)


@app.command("run")
def run_pipeline_command(
    pipeline_run_id: str = typer.Option(
        ...,
        "--pipeline-run-id",
        help="Pipeline run ID to execute.",
    ),
) -> None:
    print("[bold cyan]SceneOps Worker - Run pipeline[/bold cyan]")
    print(f"pipeline: {pipeline_run_id}")

    async def _run() -> object:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            return await PipelineRunner(context).run(pipeline_run_id)

    pipeline_run = run_cli_async(_run)

    print(f"[bold green]Done.[/bold green] status={pipeline_run.status.value}")
