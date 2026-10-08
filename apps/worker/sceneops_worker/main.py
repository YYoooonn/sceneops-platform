import typer

from sceneops_worker.cli import jobs, pipelines
from sceneops_worker.cli.recovery import recover_command
from sceneops_worker.cli.status import execution_status_command

app = typer.Typer(
    name="sceneops-worker",
    help="SceneOps Drive worker CLI",
    no_args_is_help=True,
)

app.add_typer(jobs.app, name="jobs")
app.add_typer(pipelines.app, name="pipelines")
app.command("recover")(recover_command)
app.command("execution-status")(execution_status_command)

if __name__ == "__main__":
    app()
