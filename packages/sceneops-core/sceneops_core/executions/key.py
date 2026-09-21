from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from sceneops_core.jobs.schemas.enums import JobType


def _exclude_keys(*excluded: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a transform that drops a fixed set of top-level keys.

    Used for the *_artifact_id lineage fields: two producer executions can
    write byte-identical content under different random artifact_ids
    (ALIGN_EPISODE re-runs at the same deterministic URI, Request 2.3 §18;
    same for VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE's
    aligned_artifact_id, Request 2.4 §30) and must still resolve to the same
    execution identity.
    """

    excluded_set = frozenset(excluded)

    def transform(params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if k not in excluded_set}

    return transform


def _export_learning_data_transform(params: dict[str, Any]) -> dict[str, Any]:
    """EXPORT_LEARNING_DATA's execution identity must be content-addressed,
    not caller-order-addressed (SceneOps V2 Request 2.5 §12): strip each
    input's lineage-only aligned_artifact_id (same reasoning as
    _exclude_keys above) and sort the remaining inputs by
    (episode_id, aligned_artifact_checksum) so two calls that pin the exact
    same set of aligned revisions in a different order still dedup to one
    execution. ``tables`` is also sorted for the same order-independence
    reason, when present.
    """

    inputs = params.get("inputs")
    transformed = dict(params)
    if isinstance(inputs, list):
        stripped = [
            {k: v for k, v in item.items() if k != "aligned_artifact_id"}
            for item in inputs
        ]
        stripped.sort(
            key=lambda item: (
                item.get("episode_id") or "",
                item.get("aligned_artifact_checksum") or "",
            )
        )
        transformed["inputs"] = stripped
    tables = params.get("tables")
    if isinstance(tables, list):
        transformed["tables"] = sorted(tables)
    return transformed


# JobType values whose execution identity must be derived from their
# full/normalized params dict via a transform, rather than used as-is --
# SceneOps V2 Request 2.3A §16/§31, extended by Request 2.5 §12 for
# EXPORT_LEARNING_DATA's list-of-inputs shape (a flat exclusion set isn't
# enough there: fields must be stripped per-item, and the list itself must
# be sorted for order-independence).
_EXECUTION_KEY_PARAM_TRANSFORMS: dict[
    JobType, Callable[[dict[str, Any]], dict[str, Any]]
] = {
    JobType.ALIGN_EPISODE: _exclude_keys("source_artifact_id"),
    JobType.VALIDATE_ALIGNED_EPISODE: _exclude_keys("aligned_artifact_id"),
    JobType.PROFILE_ALIGNED_EPISODE: _exclude_keys("aligned_artifact_id"),
    JobType.EXPORT_LEARNING_DATA: _export_learning_data_transform,
}


def params_for_execution_key(
    job_type: JobType, params: dict[str, Any]
) -> dict[str, Any]:
    """Generic JobType-dispatched transform from "full normalized job
    params" to "the subset/shape that defines execution identity". Default
    is a no-op (identity) for every JobType not listed in
    _EXECUTION_KEY_PARAM_TRANSFORMS. Keeps compute_execution_key() itself a
    pure hash over whatever params dict it's given; this function is where
    the "which params are semantic identity vs. incidental provenance"
    judgment call lives (Request 2.3A §12/§31, Request 2.5 §12).
    """
    transform = _EXECUTION_KEY_PARAM_TRANSFORMS.get(job_type)
    if transform is None:
        return params
    return transform(params)


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
