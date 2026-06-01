from __future__ import annotations

from sceneops_worker.datasets.profiling.base import DatasetProfiler
from sceneops_worker.datasets.profiling.standard_profiler import StandardDatasetProfiler


_DATASET_PROFILER_REGISTRY: dict[str, type[DatasetProfiler]] = {
    "standard-dataset-profiler": StandardDatasetProfiler,
}


def create_dataset_profiler(
    profiler_id: str = "standard-dataset-profiler",
) -> DatasetProfiler:
    try:
        profiler_cls = _DATASET_PROFILER_REGISTRY[profiler_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported dataset profiler: {profiler_id}") from exc

    return profiler_cls()
