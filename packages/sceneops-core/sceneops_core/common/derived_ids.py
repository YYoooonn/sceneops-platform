"""Deterministic identity of derived, checksum-pinned manifest revisions.

A derived manifest revision is identified by the checksum of its canonical
bytes. Its ArtifactRecord id is a hash of (kind prefix, logical id,
checksum), so a retry that rebuilds identical bytes converges on the same
record, and a different revision can never reuse an id (ADR-007 §33.1).
"""

from __future__ import annotations

import hashlib
from typing import Final

from .canonical_json import canonical_json_bytes

DERIVED_ARTIFACT_ID_SCHEMA_V1: Final = "sceneops.derived_artifact_id/v1"
_DIGEST_HEX_LENGTH: Final = 32


def derived_artifact_id(*, prefix: str, logical_id: str, checksum: str) -> str:
    document = {
        "derived_artifact_id_schema": DERIVED_ARTIFACT_ID_SCHEMA_V1,
        "prefix": prefix,
        "logical_id": logical_id,
        "checksum": checksum,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{prefix}-{digest[:_DIGEST_HEX_LENGTH]}"


def label_set_artifact_id(*, label_set_id: str, checksum: str) -> str:
    return derived_artifact_id(
        prefix="labelset", logical_id=label_set_id, checksum=checksum
    )


def sample_view_artifact_id(*, scene_id: str, checksum: str) -> str:
    return derived_artifact_id(
        prefix="sampleview", logical_id=scene_id, checksum=checksum
    )


def scenario_set_artifact_id(*, scenario_set_id: str, checksum: str) -> str:
    return derived_artifact_id(
        prefix="scenarioset", logical_id=scenario_set_id, checksum=checksum
    )


def prediction_manifest_artifact_id(*, inference_run_id: str, checksum: str) -> str:
    return derived_artifact_id(
        prefix="predmanifest", logical_id=inference_run_id, checksum=checksum
    )


def aligned_episode_artifact_id(*, episode_id: str, checksum: str) -> str:
    return derived_artifact_id(
        prefix="aligned", logical_id=episode_id, checksum=checksum
    )


__all__ = [
    "DERIVED_ARTIFACT_ID_SCHEMA_V1",
    "aligned_episode_artifact_id",
    "derived_artifact_id",
    "label_set_artifact_id",
    "prediction_manifest_artifact_id",
    "sample_view_artifact_id",
    "scenario_set_artifact_id",
]
