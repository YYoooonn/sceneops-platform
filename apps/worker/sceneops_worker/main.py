import typer

from sceneops_worker.commands.evaluate import app as evaluate_app
from sceneops_worker.commands.ingest import app as ingest_app
from sceneops_worker.commands.predict import app as predict_app

app = typer.Typer(
    name="sceneops-worker",
    help="SceneOps Drive worker CLI",
    no_args_is_help=True,
)

app.add_typer(ingest_app, name="ingest")
app.add_typer(predict_app, name="predict")
app.add_typer(evaluate_app, name="evaluate")


if __name__ == "__main__":
    app()
