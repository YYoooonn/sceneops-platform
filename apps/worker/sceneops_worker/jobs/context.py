from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# from sceneops_db.datasets import DatasetRepository, DatasetVersionRepository
# from sceneops_db.jobs import JobEventRepository, JobRepository
# from sceneops_worker.config import WorkerSettings


@dataclass(frozen=True)
class JobContext:
    raw_data_root: Path
    manifest_root: Path
    artifact_root: Path
    runs_root: Path

    default_dataset_id: str
    default_dataset_version: str

    # settings: WorkerSettings
    # job_repository: JobRepository
    # event_repository: JobEventRepository
    # dataset_repository: DatasetRepository
    # dataset_version_repository: DatasetVersionRepository
