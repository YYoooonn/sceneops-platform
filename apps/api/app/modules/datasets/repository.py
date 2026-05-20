from typing import Any, Protocol


class DatasetRepository(Protocol):
    def list_datasets(self) -> list[dict[str, Any]]: ...

    def get_dataset_version(
        self,
        dataset_id: str,
        dataset_version: str,
    ) -> dict[str, Any] | None: ...

    def list_scenes(
        self,
        dataset_id: str,
        dataset_version: str,
    ) -> list[dict[str, Any]]: ...

    def get_scene(
        self,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> dict[str, Any] | None: ...

    def list_samples_by_scene(
        self,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> list[dict[str, Any]]: ...

    def get_sample(
        self,
        dataset_id: str,
        dataset_version: str,
        sample_id: str,
    ) -> dict[str, Any] | None: ...
