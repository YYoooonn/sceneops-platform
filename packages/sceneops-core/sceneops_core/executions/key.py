from __future__ import annotations

import hashlib
import json
from typing import Any

from sceneops_core.jobs.schemas.enums import JobType

# JobType values whose execution identity must exclude one or more keys
# present in their full/normalized params dict -- SceneOps V2 Request 2.3A
# §16/§31. In every case here, an *_artifact_id field is lineage/provenance
# metadata (which producer execution wrote the bytes), not content identity
# -- two producer executions can write byte-identical content under
# different random artifact_ids (ALIGN_EPISODE re-runs at the same
# deterministic URI, Request 2.3 §18; the same is equally true of
# VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE's aligned_artifact_id,
# Request 2.4 §30) and must still resolve to the same execution identity.
_EXECUTION_KEY_EXCLUDED_PARAMS: dict[JobType, frozenset[str]] = {
    JobType.ALIGN_EPISODE: frozenset({"source_artifact_id"}),
    JobType.VALIDATE_ALIGNED_EPISODE: frozenset({"aligned_artifact_id"}),
    JobType.PROFILE_ALIGNED_EPISODE: frozenset({"aligned_artifact_id"}),
}


def params_for_execution_key(
    job_type: JobType, params: dict[str, Any]
) -> dict[str, Any]:
    """Generic JobType-dispatched transform from "full normalized job
    params" to "the subset that defines execution identity". Default is a
    no-op (identity) for every JobType -- only ALIGN_EPISODE currently
    excludes anything. Keeps compute_execution_key() itself a pure hash over
    whatever params dict it's given; this function is where the "which
    params are semantic identity vs. incidental provenance" judgment call
    lives (Request 2.3A §12/§31).
    """
    excluded = _EXECUTION_KEY_EXCLUDED_PARAMS.get(job_type)
    if not excluded:
        return params
    return {k: v for k, v in params.items() if k not in excluded}


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
