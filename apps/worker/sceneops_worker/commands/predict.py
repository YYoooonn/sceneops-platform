import typer
from rich import print

from sceneops_worker.config import get_settings
from sceneops_worker.predictions.mock_detection import generate_mock_predictions

app = typer.Typer(
    help="Prediction generation commands.",
    no_args_is_help=True,
)


@app.command("mock-detection")
def generate_mock_detection_command(
    dataset_id: str | None = typer.Option(None, "--dataset-id"),
    dataset_version: str | None = typer.Option(None, "--dataset-version"),
    model_id: str = typer.Option("centerpoint-mock", "--model-id"),
    model_version: str = typer.Option("v0", "--model-version"),
    run_id: str = typer.Option("run-centerpoint-mock-001", "--run-id"),
    max_samples: int | None = typer.Option(None, "--max-samples"),
    seed: int = typer.Option(42, "--seed"),
) -> None:
    settings = get_settings()

    resolved_dataset_id = dataset_id or settings.default_dataset_id
    resolved_dataset_version = dataset_version or settings.default_dataset_version

    print(
        "[bold cyan]SceneOps Worker - Generate mock detection predictions[/bold cyan]"
    )
    print(f"dataset: {resolved_dataset_id}/{resolved_dataset_version}")
    print(f"run: {run_id}")

    generate_mock_predictions(
        manifest_root=settings.manifest_root,
        runs_root=settings.runs_root,
        dataset_id=resolved_dataset_id,
        dataset_version=resolved_dataset_version,
        model_id=model_id,
        model_version=model_version,
        run_id=run_id,
        max_samples=max_samples,
        seed=seed,
    )

    print("[bold green]Done.[/bold green]")
