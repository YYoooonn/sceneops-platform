from __future__ import annotations

from sceneops_core.datasets.schemas import DatasetType
from sceneops_worker.datasets.ingestion.base import DatasetIngestor
from sceneops_worker.datasets.ingestion.nuscenes import NuScenesDatasetIngestor


_DATASET_INGESTOR_REGISTRY: dict[DatasetType, type[DatasetIngestor]] = {
    DatasetType.NUSCENES: NuScenesDatasetIngestor,
}


def create_dataset_ingestor(dataset_type: DatasetType) -> DatasetIngestor:
    try:
        ingestor_cls = _DATASET_INGESTOR_REGISTRY[dataset_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported dataset type: {dataset_type}") from exc

    return ingestor_cls()
