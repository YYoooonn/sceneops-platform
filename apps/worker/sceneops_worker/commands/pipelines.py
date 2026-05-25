from __future__ import annotations

import asyncio

import typer
from rich import print

from sceneops_worker.jobs.store import PostgresJobStore
from sceneops_worker.jobs.factory import create_job_runner
from sceneops_worker.pipelines.runner import PipelineRunner
from sceneops_worker.pipelines.store import PostgresPipelineStore

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

    job_store = PostgresJobStore()
    job_runner = create_job_runner(job_store=job_store)

    pipeline_runner = PipelineRunner(
        pipeline_store=PostgresPipelineStore(),
        job_store=job_store,
        job_runner=job_runner,
    )

    pipeline_run = asyncio.run(pipeline_runner.run(pipeline_run_id))

    print(f"[bold green]Done.[/bold green] status={pipeline_run.status.value}")
