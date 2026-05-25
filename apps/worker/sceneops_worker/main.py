import typer

from sceneops_worker.commands import evaluate, ingest, jobs, pipelines, predict

app = typer.Typer(
    name="sceneops-worker",
    help="SceneOps Drive worker CLI",
    no_args_is_help=True,
)

app.add_typer(ingest.app, name="ingest")
app.add_typer(predict.app, name="predict")
app.add_typer(evaluate.app, name="evaluate")
app.add_typer(jobs.app, name="jobs")
app.add_typer(pipelines.app, name="pipelines")

if __name__ == "__main__":
    app()
