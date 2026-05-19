import typer
from rich import print

from sceneops_worker.config import get_settings
from sceneops_worker.nuscenes.ingest import ingest_nuscenes, IngestMode

app = typer.Typer()

app = typer.Typer(
    name="sceneops-worker",
    help="SceneOps Drive worker CLI",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """SceneOps Drive batch worker."""
    pass


@app.command("ingest-nuscenes")
def ingest_nuscenes_command(
    dataset_id: str | None = typer.Option(None, "--dataset-id"),
    dataset_version: str | None = typer.Option(None, "--dataset-version"),
    max_scenes: int | None = typer.Option(None, "--max-scenes"),
    mode: IngestMode = typer.Option(
        IngestMode.UPSERT,
        "--mode",
        help="Ingestion mode: replace, append, or upsert.",
    ),
) -> None:
    settings = get_settings()

    resolved_dataset_id = dataset_id or settings.default_dataset_id
    resolved_dataset_version = dataset_version or settings.default_dataset_version

    print("[bold cyan]SceneOps Drive - nuScenes ingestion[/bold cyan]")
    print(f"dataset: {resolved_dataset_id}/{resolved_dataset_version}")
    print(f"raw data root: {settings.raw_data_root}")
    print(f"manifest root: {settings.manifest_root}")

    if resolved_dataset_id != "nuscenes":
        raise NotImplementedError("Only supports nuscenes for now")

    ingest_nuscenes(
        dataroot=settings.raw_data_root,
        dataset_id=resolved_dataset_id,
        dataset_version=resolved_dataset_version,
        manifest_root=settings.manifest_root,
        max_scenes=max_scenes,
        mode=mode,
    )

    print("[bold green]Done.[/bold green]")


if __name__ == "__main__":
    app()
