"""Deterministic canonical unit identity (ADR-007 §18.1).

    external unit id   = f(dataset_id, dataset_version, domain, external_format, source_unit_key)
    recording unit id  = f(dataset_id, dataset_version, domain, robot_run_id, unit_key)

The id is a hash of a canonical-JSON identity document, so it is injective
over its inputs (no separator ambiguity), fits the 128-character id columns,
and never depends on storage location, producer configuration or execution
state. A rebuild that keeps the unit key keeps the id, which is what lets a
replacement repoint an existing record instead of creating a new one.

Changing anything in the identity document is an identity-affecting
migration; ``unit_id_schema`` makes such a change explicit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final, Literal

from sceneops_core.common.canonical_json import canonical_json_bytes

from .sources import (
    ExternalUnitSource,
    RecordingSegmentSource,
    UnitSource,
    UnitSourceKind,
)

UNIT_ID_SCHEMA_V1: Final = "sceneops.unit_id/v1"
_UNIT_ID_DIGEST_HEX_LENGTH: Final = 32

CanonicalDomain = Literal["scene", "episode"]


@dataclass(frozen=True)
class UnitSourceProjection:
    """The searchable identity projection of a unit's source block, as
    canonical records store it (ADR-007 §13.3)."""

    source_kind: UnitSourceKind
    external_format: str | None
    robot_run_id: str | None
    source_unit_key: str


def project_unit_source(source: UnitSource) -> UnitSourceProjection:
    if isinstance(source, ExternalUnitSource):
        return UnitSourceProjection(
            source_kind=UnitSourceKind.EXTERNAL,
            external_format=source.format,
            robot_run_id=None,
            source_unit_key=source.source_unit_key,
        )
    if isinstance(source, RecordingSegmentSource):
        return UnitSourceProjection(
            source_kind=UnitSourceKind.RECORDING,
            external_format=None,
            robot_run_id=source.robot_run_id,
            source_unit_key=source.unit_key,
        )
    raise TypeError(f"unsupported unit source: {type(source).__name__}")


def canonical_unit_id(
    *,
    domain: CanonicalDomain,
    dataset_id: str,
    dataset_version: str,
    source: UnitSource,
) -> str:
    projection = project_unit_source(source)
    if projection.source_kind == UnitSourceKind.EXTERNAL:
        identity = {
            "source_kind": "external",
            "external_format": projection.external_format,
            "source_unit_key": projection.source_unit_key,
        }
    else:
        identity = {
            "source_kind": "recording",
            "robot_run_id": projection.robot_run_id,
            "unit_key": projection.source_unit_key,
        }
    document = {
        "unit_id_schema": UNIT_ID_SCHEMA_V1,
        "domain": domain,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source": identity,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{domain}-{digest[:_UNIT_ID_DIGEST_HEX_LENGTH]}"


__all__ = [
    "UNIT_ID_SCHEMA_V1",
    "CanonicalDomain",
    "UnitSourceProjection",
    "canonical_unit_id",
    "project_unit_source",
]
