from __future__ import annotations

import hashlib
import json

from sceneops_core.episodes.alignment import (
    ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
    ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
)

from .policy import CurationPolicy
from .semantics import CURATION_SEMANTICS_VERSION

# CurationPolicy fields whose list values are semantically unordered sets
# (Request 2.6 §10 "equivalent policies should hash identically") -- sorted
# before hashing so [a, b] and [b, a] never produce different identities,
# mirroring learning_data_export_id's sorted-checksums precedent.
_UNORDERED_LIST_FIELDS = (
    "required_observation_channels",
    "required_action_channels",
    "allowed_tasks",
    "allowed_outcomes",
)


def canonical_curation_policy_payload(policy: CurationPolicy) -> dict:
    """Order-independent JSON-safe dict for one CurationPolicy -- the single
    source of truth for both curation_policy_hash and the CURATE_EPISODES
    execution-key params transform, so the two can never drift apart."""
    payload = policy.model_dump(mode="json", exclude_none=True)
    for field in _UNORDERED_LIST_FIELDS:
        value = payload.get(field)
        if isinstance(value, list):
            payload[field] = sorted(value)
    return payload


def curation_policy_hash(policy: CurationPolicy) -> str:
    """Deterministic identity for one CurationPolicy's content (SceneOps V2
    Request 2.6 §10) -- canonical serialization, order-independent list
    fields, sorted dict keys."""
    canonical = json.dumps(
        canonical_curation_policy_payload(policy),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def episode_curation_id(
    *,
    source_export_checksum: str,
    policy_hash: str,
    curation_semantics_version: str = CURATION_SEMANTICS_VERSION,
    validation_semantics_version: str = ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
    profile_semantics_version: str = ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
) -> str:
    """Semantic curation identity (SceneOps V2 Request 2.6 §10, extended by
    Request 2.6A §2): derived from the pinned source learning-data export's
    own content checksum + the canonical policy hash + the evaluator's own
    semantics version + the validation/profile semantics versions that
    directly produced the CurationCandidateFacts this run's decisions were
    evaluated against -- never from job_id, ArtifactRecord artifact_id, or
    execution timestamp.

    The last two parameters close a reproducibility gap (Request 2.6A §1):
    CurationEvaluator only ever sees facts recomputed via
    AlignedEpisodeValidator/AlignedEpisodeProfiler, so a future change to
    either's semantics version can change *what a candidate's facts are*
    without curation_policy_hash or CURATION_SEMANTICS_VERSION changing at
    all. Two CURATE_EPISODES executions over the same source revision,
    equivalent policy, AND identical analysis semantics always produce the
    same curation_id; a change to either analysis semantics version always
    changes it.
    """
    payload = {
        "source_export_checksum": source_export_checksum.removeprefix("sha256:"),
        "policy_hash": policy_hash,
        "curation_semantics_version": curation_semantics_version,
        "validation_semantics_version": validation_semantics_version,
        "profile_semantics_version": profile_semantics_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
