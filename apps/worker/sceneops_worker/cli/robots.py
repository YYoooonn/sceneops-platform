from __future__ import annotations

import typer
from rich import print

from sceneops_db.session import async_session_scope
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.robots.registration import (
    RobotRunRegistration,
    register_robot_run,
)

app = typer.Typer(
    help="RobotRun registration commands.",
    no_args_is_help=True,
)


@app.command("register")
def register_command(
    manifest_uri: str = typer.Option(
        ...,
        "--manifest-uri",
        help="URI of a RobotRunManifest written by the Recording Publisher "
        "(python -m sceneops_integrations.recording publish).",
    ),
) -> None:
    """REGISTER_ROBOT_RUN from a shell: runs the same registrar as the
    REGISTER_ROBOT_RUN job handler (``POST /robot-runs:register``), in this
    process. Verifies the published manifest and recording, then registers
    both ArtifactRecords and the RobotRunRecord atomically. An identical
    retry reports ``created=False``; a different manifest for an
    already-registered run_id fails."""
    print("[bold cyan]SceneOps Worker - Register RobotRun[/bold cyan]")
    print(f"manifest_uri={manifest_uri}")

    async def _run() -> RobotRunRegistration:
        async with async_session_scope() as session:
            context = create_worker_context(session, worker_id="cli")
            return await register_robot_run(context=context, manifest_uri=manifest_uri)

    registration = run_cli_async(_run)
    robot_run = registration.robot_run
    print(
        f"[bold green]Done.[/bold green] "
        f"created={registration.created} "
        f"run_id={robot_run.run_id} "
        f"robot_id={robot_run.robot_id} "
        f"recording_artifact_id={robot_run.recording_artifact_id} "
        f"manifest_artifact_id={robot_run.manifest_artifact_id} "
        f"manifest_checksum={robot_run.manifest_checksum}"
    )
