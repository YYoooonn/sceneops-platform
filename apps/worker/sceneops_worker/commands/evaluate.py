import typer
from rich import print

from sceneops_worker.config import get_settings
from sceneops_worker.evaluation.detection import evaluate_detection_run

app = typer.Typer(
    help="Evaluation commands.",
    no_args_is_help=True,
)


@app.command("detection")
def evaluate_detection_command(
    dataset_id: str | None = typer.Option(None, "--dataset-id"),
    dataset_version: str | None = typer.Option(None, "--dataset-version"),
    inference_run_id: str = typer.Option(
        "run-centerpoint-mock-001",
        "--inference-run-id",
    ),
    evaluation_run_id: str = typer.Option(
        "eval-centerpoint-mock-001",
        "--evaluation-run-id",
    ),
    match_distance_m: float = typer.Option(2.0, "--match-distance-m"),
) -> None:
    settings = get_settings()

    resolved_dataset_id = dataset_id or settings.default_dataset_id
    resolved_dataset_version = dataset_version or settings.default_dataset_version

    print("[bold cyan]SceneOps Worker - Evaluate detection run[/bold cyan]")
    print(f"dataset: {resolved_dataset_id}/{resolved_dataset_version}")
    print(f"inference run: {inference_run_id}")
    print(f"evaluation run: {evaluation_run_id}")

    evaluate_detection_run(
        manifest_root=settings.manifest_root,
        runs_root=settings.runs_root,
        dataset_id=resolved_dataset_id,
        dataset_version=resolved_dataset_version,
        inference_run_id=inference_run_id,
        evaluation_run_id=evaluation_run_id,
        match_distance_m=match_distance_m,
    )

    print("[bold green]Done.[/bold green]")
