from __future__ import annotations

from sceneops_worker.datasets.validation.base import DatasetValidator
from sceneops_worker.datasets.validation.manifest_validator import (
    ManifestDatasetValidator,
)


_DATASET_VALIDATOR_REGISTRY: dict[str, type[DatasetValidator]] = {
    "manifest-validator": ManifestDatasetValidator,
}


def create_dataset_validator(
    validator_id: str = "manifest-validator",
) -> DatasetValidator:
    try:
        validator_cls = _DATASET_VALIDATOR_REGISTRY[validator_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported dataset validator: {validator_id}") from exc

    return validator_cls()
