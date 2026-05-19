import typer
from rich import print

from sceneops_worker.config import get_settings
from sceneops_worker.nuscenes.ingest_mini import ingest_nuscenes_mini

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


@app.command("ingest-nuscenes-mini")
def ingest_nuscenes_mini_command(
    max_scenes: int | None = typer.Option(
        None,
        "--max-scenes",
        help="Limit number of scenes for initial ingestion.",
    ),
) -> None:
    settings = get_settings()

    print("[bold cyan]SceneOps Drive - nuScenes mini ingestion[/bold cyan]")
    print(f"dataset: {settings.dataset_id}/{settings.dataset_version}")
    print(f"nuScenes root: {settings.nuscenes_root}")
    print(f"manifest root: {settings.manifest_root}")

    ingest_nuscenes_mini(
        dataroot=settings.nuscenes_root,
        version=settings.dataset_version,
        manifest_root=settings.manifest_root,
        max_scenes=max_scenes,
    )

    print("[bold green]Done.[/bold green]")


if __name__ == "__main__":
    app()
