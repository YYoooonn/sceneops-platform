import json
from pathlib import Path
from typing import Any

from sceneops_core.paths.datasets import (
    dataset_manifest_path,
    sample_manifest_path,
    scene_manifest_path,
    scenes_index_path,
)


class LocalManifestDatasetRepository:
    def __init__(self, manifest_root: Path) -> None:
        self.manifest_root = manifest_root

    def list_datasets(self) -> list[dict[str, Any]]:
        datasets_root = self.manifest_root / "datasets"

        if not datasets_root.exists():
            return []

        datasets: list[dict[str, Any]] = []

        for dataset_dir in sorted(datasets_root.iterdir()):
            if not dataset_dir.is_dir():
                continue

            versions = self._list_versions(dataset_dir)

            datasets.append(
                {
                    "datasetId": dataset_dir.name,
                    "versions": versions,
                }
            )

        return datasets

    def get_dataset_version(
        self,
        dataset_id: str,
        dataset_version: str,
    ) -> dict[str, Any] | None:
        path = dataset_manifest_path(
            manifest_root=self.manifest_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        return self._read_json_or_none(path)

    def list_scenes(
        self,
        dataset_id: str,
        dataset_version: str,
    ) -> list[dict[str, Any]]:
        path = scenes_index_path(
            manifest_root=self.manifest_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        data = self._read_json_or_none(path)

        if data is None:
            return []

        if not isinstance(data, list):
            return []

        return data

    def get_scene(
        self,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> dict[str, Any] | None:
        path = scene_manifest_path(
            manifest_root=self.manifest_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
        )
        return self._read_json_or_none(path)

    def list_samples_by_scene(
        self,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> list[dict[str, Any]]:
        scene = self.get_scene(dataset_id, dataset_version, scene_id)

        if scene is None:
            return []

        samples: list[dict[str, Any]] = []

        for sample_id in scene.get("sampleIds", []):
            sample = self.get_sample(dataset_id, dataset_version, sample_id)
            if sample is not None:
                samples.append(sample)

        return samples

    def get_sample(
        self,
        dataset_id: str,
        dataset_version: str,
        sample_id: str,
    ) -> dict[str, Any] | None:
        path = sample_manifest_path(
            manifest_root=self.manifest_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            sample_id=sample_id,
        )
        return self._read_json_or_none(path)

    def _version_root(self, dataset_id: str, dataset_version: str) -> Path:
        return (
            self.manifest_root / "datasets" / dataset_id / "versions" / dataset_version
        )

    def _list_versions(self, dataset_dir: Path) -> list[str]:
        versions_root = dataset_dir / "versions"

        if not versions_root.exists():
            return []

        return [
            version_dir.name
            for version_dir in sorted(versions_root.iterdir())
            if version_dir.is_dir()
        ]

    def _read_json_or_none(self, path: Path) -> Any | None:
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
