from __future__ import annotations

import sys

import typer
from rich import print

from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.pipelines.runner import PipelineRunner
from sceneops_worker.pipelines.task_runner import PipelineTaskRunner

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


def run_pipeline_task_command(
    pipeline_run_id: str = typer.Option(
        ...,
        "--pipeline-run-id",
        help="Pipeline run ID.",
    ),
    task_id: str = typer.Option(
        ...,
        "--task-id",
        help="Task ID to execute (maps to Airflow task_id).",
    ),
) -> None:
    """Execute one pipeline task.

    In Airflow terms this is one Task invocation.  The container exit code
    reflects success (0) or failure (non-zero) so DockerOperator /
    KubernetesPodOperator can use it as the task outcome.
    """
    print("[bold cyan]SceneOps Worker - Run pipeline task[/bold cyan]")
    print(f"pipeline_run_id : {pipeline_run_id}")
    print(f"task_id         : {task_id}")

    async def _run() -> object:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            return await PipelineTaskRunner(context).run(
                pipeline_run_id=pipeline_run_id,
                task_id=task_id,
            )

    try:
        job = run_cli_async(_run)
    except Exception as exc:
        print(f"[bold red]Error:[/bold red] {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None

    print("[bold green]Done.[/bold green]")
    print(f"  job_id    : {job.job_id}")
    print(f"  status    : {job.status.value}")
