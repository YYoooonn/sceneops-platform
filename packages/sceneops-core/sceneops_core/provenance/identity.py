"""Deterministic canonical unit identity (ADR-007 §18.1)::

    unit id = f(dataset_id, dataset_version, domain, robot_run_id, unit_key)

The id is a hash of a canonical-JSON identity document, so it is injective
over its inputs (no separator ambiguity), fits the 128-character id columns,
and never depends on storage location, producer configuration or execution
state. A rebuild that keeps the unit key keeps the id, which is what lets a
replacement repoint an existing record instead of creating a new one.

Changing anything in the identity document is an identity-affecting
migration; ``unit_id_schema`` makes such a change explicit. The document
keeps its ``source_kind = "recording"`` member, so ids are unchanged from
when a second source kind existed (§29.9).
"""

from __future__ import annotations

import hashlib
from typing import Final, Literal

from sceneops_core.common.canonical_json import canonical_json_bytes

from .sources import RecordingSegmentSource

UNIT_ID_SCHEMA_V1: Final = "sceneops.unit_id/v1"
_UNIT_ID_DIGEST_HEX_LENGTH: Final = 32

CanonicalDomain = Literal["scene", "episode"]


def canonical_unit_id(
    *,
    domain: CanonicalDomain,
    dataset_id: str,
    dataset_version: str,
    source: RecordingSegmentSource,
) -> str:
    document = {
        "unit_id_schema": UNIT_ID_SCHEMA_V1,
        "domain": domain,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source": {
            "source_kind": source.source_kind,
            "robot_run_id": source.robot_run_id,
            "unit_key": source.unit_key,
        },
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{domain}-{digest[:_UNIT_ID_DIGEST_HEX_LENGTH]}"


__all__ = [
    "UNIT_ID_SCHEMA_V1",
    "CanonicalDomain",
    "canonical_unit_id",
]
