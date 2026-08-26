from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_execution_key(
    *,
    kind: str,
    type: str,
    dataset_id: str | None,
    dataset_version: str | None,
    model_id: str | None = None,
    model_version: str | None = None,
    params: dict[str, Any],
) -> str:
    """Deterministic key identifying "this exact unit of work".

    Same inputs always produce the same key; any change to any input field
    changes the key. Used to detect and reuse already-succeeded (or
    in-flight) Job/PipelineRun executions instead of redoing identical work.
    """
    payload = {
        "kind": kind,
        "type": type,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "model_id": model_id,
        "model_version": model_version,
        "params": params,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{type}:{digest[:24]}"
