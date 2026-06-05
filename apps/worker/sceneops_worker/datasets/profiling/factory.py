from __future__ import annotations

from sceneops_worker.datasets.profiling.base import DatasetProfiler


_DATASET_PROFILER_REGISTRY: dict[str, type[DatasetProfiler]] = {}


def create_dataset_profiler(
    profiler_id: str = "standard-dataset-profiler",
) -> DatasetProfiler:
    try:
        profiler_cls = _DATASET_PROFILER_REGISTRY[profiler_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported dataset profiler: {profiler_id}") from exc

    return profiler_cls()
