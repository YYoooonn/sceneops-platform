from pathlib import Path


def dataset_version_root(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
) -> Path:
    return manifest_root / "datasets" / dataset_id / "versions" / dataset_version


def inference_run_root(
    *,
    runs_root: Path,
    run_id: str,
) -> Path:
    return runs_root / "inference" / run_id


def evaluation_run_root(
    *,
    runs_root: Path,
    evaluation_run_id: str,
) -> Path:
    return runs_root / "evaluations" / evaluation_run_id
