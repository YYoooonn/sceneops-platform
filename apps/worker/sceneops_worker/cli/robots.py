from __future__ import annotations

import typer
from rich import print

from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context

app = typer.Typer(
    help="Robot / RobotRun registration commands.",
    no_args_is_help=True,
)


@app.command("register-run")
def register_run_command(
    robot_id: str = typer.Option(..., "--robot-id"),
    run_id: str = typer.Option(..., "--run-id"),
    mcap_uri: str = typer.Option(..., "--mcap-uri"),
    platform: str = typer.Option("nuscenes-can-replay", "--platform"),
) -> None:
    """Upsert a Robot + RobotRun so ingest_robot_states has something to attach to.

    Stopgap CLI, not a REST API — Robot/RobotRun registration doesn't have
    one yet (docs/robot-data-model.md §6 lists this as an open gap). This is
    the fastest way to get a row in place before dispatching
    ingest_robot_states against a recorded bag.
    """
    print("[bold cyan]SceneOps Worker - Register robot run[/bold cyan]")
    print(f"robot_id={robot_id} run_id={run_id} mcap_uri={mcap_uri}")

    async def _run() -> RobotRunRecord:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            await context.robot_store.upsert_robot(
                RobotRecord(robot_id=robot_id, platform=platform)
            )
            robot_run = await context.robot_store.upsert_run(
                RobotRunRecord(run_id=run_id, robot_id=robot_id, mcap_uri=mcap_uri)
            )
            await context.commit()
            return robot_run

    robot_run = run_cli_async(_run)
    print(
        f"[bold green]Done.[/bold green] "
        f"run_id={robot_run.run_id} status={robot_run.status.value}"
    )
