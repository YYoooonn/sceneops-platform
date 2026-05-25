from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobExecutionContext:
    raw_data_root: Path
    manifest_root: Path
    artifact_root: Path
    runs_root: Path

    default_dataset_id: str
    default_dataset_version: str
