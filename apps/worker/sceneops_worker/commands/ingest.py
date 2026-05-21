import typer
from rich import print

from sceneops_worker.config import get_settings
from sceneops_worker.ingest.nuscenes import IngestMode, ingest_nuscenes

app = typer.Typer(
    help="Dataset ingestion commands.",
    no_args_is_help=True,
)


@app.command("nuscenes")
def ingest_nuscenes_command(
    dataset_id: str | None = typer.Option(None, "--dataset-id"),
    dataset_version: str | None = typer.Option(None, "--dataset-version"),
    max_scenes: int | None = typer.Option(None, "--max-scenes"),
    mode: IngestMode = typer.Option(IngestMode.UPSERT, "--mode"),
) -> None:
    settings = get_settings()

    resolved_dataset_id = dataset_id or settings.default_dataset_id
    resolved_dataset_version = dataset_version or settings.default_dataset_version

    print("[bold cyan]SceneOps Worker - Ingest nuScenes[/bold cyan]")
    print(f"dataset: {resolved_dataset_id}/{resolved_dataset_version}")

    ingest_nuscenes(
        dataroot=settings.raw_data_root,
        dataset_id=resolved_dataset_id,
        dataset_version=resolved_dataset_version,
        manifest_root=settings.manifest_root,
        max_scenes=max_scenes,
        mode=mode,
    )

    print("[bold green]Done.[/bold green]")
