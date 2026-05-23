from __future__ import annotations

import asyncio

import typer
from rich import print

from sceneops_worker.config import get_settings
from sceneops_worker.jobs.executors import JobExecutionContext, JobExecutor
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.jobs.store import PostgresJobStore

app = typer.Typer(
    help="Job execution commands.",
    no_args_is_help=True,
)


@app.command("run")
def run_job_command(
    job_id: str = typer.Option(..., "--job-id", help="Job ID to execute."),
) -> None:
    settings = get_settings()

    print("[bold cyan]SceneOps Worker - Run job[/bold cyan]")
    print(f"job: {job_id}")

    job_store = PostgresJobStore()

    context = JobExecutionContext(
        raw_data_root=settings.raw_data_root,
        manifest_root=settings.manifest_root,
        artifact_root=settings.artifact_root,
        runs_root=settings.runs_root,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )

    job_executor = JobExecutor(context)
    job_runner = JobRunner(
        job_store=job_store,
        job_executor=job_executor,
        worker_id=settings.worker_id,
    )

    job = asyncio.run(job_runner.run(job_id))

    print(f"[bold green]Done.[/bold green] status={job.status.value}")
