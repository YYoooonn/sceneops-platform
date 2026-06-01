from __future__ import annotations

import typer
from rich import print

from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.jobs.factory import create_job_runner

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
        job_runner = create_job_runner()
        return await job_runner.run(job_id)

    job = run_cli_async(_run)

    print(f"[bold green]Done.[/bold green] status={job.status.value}")
