"""Deterministic canonical unit identity (ADR-015 §3, MS-6)::

    unit_id = <domain>-<sha256(canonical_json({
        "unit_id_schema": "sceneops.unit_id/v2",
        "domain":         "scene" | "episode",
        "source_kind":    "recording" | "content",
        "source_id":      robot_run_id | content_id,
        "unit_key":       the source binding's unit key, verbatim
    }))[:32]>

A unit id answers "which logical unit of which source". Its inputs come
from the unit's source binding alone, so it never depends on:

- a DatasetVersion: the same unit in two compositions is one unit,
  referenced twice;
- a SourceSnapshot: ``content_id`` is a function of the bytes, so every
  declaration of the same content yields the same ids;
- the source revision checksum, the window, the producer or its build
  configuration: those decide a *revision* of the unit
  (``sceneops_core.provenance.revisions``), not the unit;
- storage location or execution context.

The identity document is canonical JSON, so the hash is injective over its
inputs (no separator ambiguity), and the id fits the 128-character id
columns. ``unit_id_schema`` makes any change to the document an explicit
identity change.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final, Literal, get_args

from sceneops_core.common.canonical_json import canonical_json_bytes

from .sources import ContentUnitSource, RecordingSegmentSource

UNIT_ID_SCHEMA_V2: Final = "sceneops.unit_id/v2"
_UNIT_ID_DIGEST_HEX_LENGTH: Final = 32

CanonicalDomain = Literal["scene", "episode"]
CANONICAL_DOMAINS: Final[tuple[CanonicalDomain, ...]] = get_args(CanonicalDomain)
_UNIT_ID_RE: Final = re.compile(
    rf"^(?P<domain>{'|'.join(CANONICAL_DOMAINS)})-[0-9a-f]{{{_UNIT_ID_DIGEST_HEX_LENGTH}}}$"
)


def _check_domain(domain: str) -> CanonicalDomain:
    if domain not in CANONICAL_DOMAINS:
        raise ValueError(
            f"domain must be one of {list(CANONICAL_DOMAINS)}, got {domain!r}"
        )
    return domain  # type: ignore[return-value]


def canonical_unit_id(
    *,
    domain: CanonicalDomain,
    source: RecordingSegmentSource | ContentUnitSource,
) -> str:
    if not isinstance(source, (RecordingSegmentSource, ContentUnitSource)):
        raise TypeError(f"source must be a source binding, got {type(source).__name__}")
    document = {
        "unit_id_schema": UNIT_ID_SCHEMA_V2,
        "domain": _check_domain(domain),
        "source_kind": source.source_kind,
        "source_id": source.source_id,
        "unit_key": source.unit_key,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{domain}-{digest[:_UNIT_ID_DIGEST_HEX_LENGTH]}"


def unit_id_domain(unit_id: str) -> CanonicalDomain:
    """The domain a unit id was derived for; anything not shaped like a
    canonical unit id is rejected."""
    match = _UNIT_ID_RE.fullmatch(unit_id) if isinstance(unit_id, str) else None
    if match is None:
        raise ValueError(f"not a canonical unit id: {unit_id!r}")
    return match["domain"]  # type: ignore[return-value]


__all__ = [
    "CANONICAL_DOMAINS",
    "UNIT_ID_SCHEMA_V2",
    "CanonicalDomain",
    "canonical_unit_id",
    "unit_id_domain",
]
