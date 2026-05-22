from pathlib import Path


def dataset_version_root(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
) -> Path:
    return (
        manifest_root
        / "datasets"
        / dataset_id
        / "versions"
        / dataset_version
    )


def dataset_manifest_path(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
) -> Path:
    return dataset_version_root(
        manifest_root=manifest_root,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    ) / "dataset.json"


def scenes_index_path(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
) -> Path:
    return dataset_version_root(
        manifest_root=manifest_root,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    ) / "scenes.json"


def scene_manifest_path(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
    scene_id: str,
) -> Path:
    return (
        dataset_version_root(
            manifest_root=manifest_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        / "scenes"
        / f"{scene_id}.json"
    )


def sample_manifest_path(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
    sample_id: str,
) -> Path:
    return (
        dataset_version_root(
            manifest_root=manifest_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        / "samples"
        / f"{sample_id}.json"
    )
