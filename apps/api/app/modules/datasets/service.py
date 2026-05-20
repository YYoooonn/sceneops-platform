from app.modules.datasets.repository import DatasetRepository


class DatasetService:
    def __init__(self, repository: DatasetRepository) -> None:
        self.repository = repository

    def list_datasets(self):
        return self.repository.list_datasets()

    def get_dataset_version(self, dataset_id: str, dataset_version: str):
        return self.repository.get_dataset_version(dataset_id, dataset_version)

    def list_scenes(self, dataset_id: str, dataset_version: str):
        return self.repository.list_scenes(dataset_id, dataset_version)

    def get_scene(self, dataset_id: str, dataset_version: str, scene_id: str):
        return self.repository.get_scene(dataset_id, dataset_version, scene_id)

    def list_samples_by_scene(
        self,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ):
        return self.repository.list_samples_by_scene(
            dataset_id,
            dataset_version,
            scene_id,
        )

    def get_sample(self, dataset_id: str, dataset_version: str, sample_id: str):
        return self.repository.get_sample(dataset_id, dataset_version, sample_id)
