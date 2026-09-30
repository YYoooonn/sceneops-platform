from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print

from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.robots.registration import (
    RobotRunCaptureRegistration,
    register_robot_run_capture,
)

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
    one yet (docs/workflows/robot-run-and-mcap.md §6 lists this as an open gap). This is
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


@app.command("register-capture")
def register_capture_command(
    robot_id: str = typer.Option(..., "--robot-id"),
    robot_run_id: str = typer.Option(..., "--robot-run-id"),
    mcap_path: Path = typer.Option(
        ..., "--mcap-path", help="Local path to a finalized (not .partial) MCAP file."
    ),
    platform: str = typer.Option("nuscenes-can-replay", "--platform"),
    capture_metadata_json: str | None = typer.Option(
        None,
        "--capture-metadata-json",
        help=(
            "Optional JSON object of Kafka execution/provenance (topic, "
            "partition, offset range, sequence range, message count) -- "
            "stored in the ArtifactRecord's own metadata field, never as "
            "new DB columns."
        ),
    ),
) -> None:
    """Register a finalized local MCAP (ros2/capture's CaptureResult.path,
    or any directly `ros2 bag record`-ed file) as a canonical RobotRun:
    upload/verify into ArtifactStore, register one ArtifactRecord + one
    RobotRunRecord, atomically. Idempotent -- an exact retry (same
    robot_run_id, same file content) returns the existing canonical state
    rather than creating anything new; a conflicting retry (same
    robot_run_id, different content) fails loudly without mutating
    existing state. See sceneops_worker.robots.registration.
    """
    print("[bold cyan]SceneOps Worker - Register RobotRun capture[/bold cyan]")
    print(f"robot_id={robot_id} robot_run_id={robot_run_id} mcap_path={mcap_path}")

    capture_metadata = (
        json.loads(capture_metadata_json) if capture_metadata_json else None
    )

    async def _run() -> RobotRunCaptureRegistration:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            return await register_robot_run_capture(
                context=context,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                mcap_path=mcap_path,
                platform=platform,
                capture_metadata=capture_metadata,
            )

    registration = run_cli_async(_run)
    print(
        f"[bold green]Done.[/bold green] "
        f"created={registration.created} "
        f"run_id={registration.robot_run.run_id} "
        f"status={registration.robot_run.status.value} "
        f"artifact_id={registration.artifact.artifact_id} "
        f"uri={registration.artifact.uri} "
        f"checksum={registration.artifact.checksum}"
    )
