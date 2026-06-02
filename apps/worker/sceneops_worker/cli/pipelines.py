from __future__ import annotations

from sceneops_worker.registry import create_runtime_store_registry
import typer
from rich import print

from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.pipelines.factory import create_pipeline_runner

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
        registry = create_runtime_store_registry()
        pipeline_runner = create_pipeline_runner(
            registry=registry,
            worker_id="cli",
        )
        return await pipeline_runner.run(pipeline_run_id)

    pipeline_run = run_cli_async(_run)

    print(f"[bold green]Done.[/bold green] status={pipeline_run.status.value}")
