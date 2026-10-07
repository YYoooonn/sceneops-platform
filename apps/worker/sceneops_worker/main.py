import typer

from sceneops_worker.cli import jobs, pipelines

app = typer.Typer(
    name="sceneops-worker",
    help="SceneOps Drive worker CLI",
    no_args_is_help=True,
)

app.add_typer(jobs.app, name="jobs")
app.add_typer(pipelines.app, name="pipelines")

if __name__ == "__main__":
    app()
