from __future__ import annotations

# Deprecated: replaced by WorkerContext in sceneops_worker.core.context.
# This stub is retained so old handler imports do not break at import time.
# All handlers will be migrated to WorkerContext in Phase 2B.

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobContext:
    """Deprecated shim. Use WorkerContext instead."""

    worker_id: str = ""
    artifact_store: Any = None
    dataset_artifact_store: Any = None
    run_artifact_store: Any = None
    dataset_registry_store: Any = None
    model_registry_store: Any = None
    run_registry_store: Any = None
    scene_registry_store: Any = None
    default_dataset_id: str = ""
    default_dataset_version: str = ""
