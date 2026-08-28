from __future__ import annotations

import sys

import typer
from rich import print

from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.pipelines.runner import PipelineRunner
from sceneops_worker.pipelines.task_execution import PipelineTaskOutcome
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


@app.command("start")
def start_pipeline_command(
    pipeline_run_id: str = typer.Option(
        ...,
        "--pipeline-run-id",
        help="Pipeline run ID to start.",
    ),
) -> None:
    """Validate and transition a pipeline run to RUNNING.

    Used as the first task of a per-task Airflow DAG, where each subsequent
    pipeline task is executed as a separate `run-pipeline-task` invocation
    rather than by `PipelineRunner.run()`'s own loop.
    """
    print("[bold cyan]SceneOps Worker - Start pipeline[/bold cyan]")
    print(f"pipeline_run_id : {pipeline_run_id}")

    async def _run() -> object:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            return await PipelineRunner(context).start(pipeline_run_id)

    try:
        pipeline_run = run_cli_async(_run)
    except Exception as exc:
        print(f"[bold red]Error:[/bold red] {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None

    print(f"[bold green]Done.[/bold green] status={pipeline_run.status.value}")


@app.command("finalize")
def finalize_pipeline_command(
    pipeline_run_id: str = typer.Option(
        ...,
        "--pipeline-run-id",
        help="Pipeline run ID to finalize.",
    ),
) -> None:
    """Roll up persisted task-run statuses into a terminal pipeline status.

    Used as the last task of a per-task Airflow DAG (trigger_rule=all_done),
    since no single process runs the whole `PipelineRunner.run()` loop when
    each task is a separate `run-pipeline-task` invocation.
    """
    print("[bold cyan]SceneOps Worker - Finalize pipeline[/bold cyan]")
    print(f"pipeline_run_id : {pipeline_run_id}")

    async def _run() -> object:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            return await PipelineRunner(context).finalize(pipeline_run_id)

    try:
        pipeline_run = run_cli_async(_run)
    except Exception as exc:
        print(f"[bold red]Error:[/bold red] {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None

    print(f"[bold green]Done.[/bold green] status={pipeline_run.status.value}")

    if pipeline_run.status.value in {"failed", "blocked"}:
        raise typer.Exit(code=1)


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
        result = run_cli_async(_run)
    except Exception as exc:
        print(f"[bold red]Error:[/bold red] {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None

    print(f"  outcome     : {result.outcome.value}")
    print(f"  task_status : {result.task_run.status.value}")

    if result.outcome == PipelineTaskOutcome.BLOCKED:
        print("[bold yellow]Blocked by quality gate.[/bold yellow]", file=sys.stderr)
        raise typer.Exit(code=1)

    print("[bold green]Done.[/bold green]")
