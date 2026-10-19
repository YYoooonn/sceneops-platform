"""The documented identity and checksum formulas, implemented from the
documentation, not imported from SceneOps.

Each function names the document it implements. Nothing here imports a
SceneOps package: a formula that disagrees with the platform is a finding
(an implementation defect or an imprecise document), never something to fit.

    canonical JSON          ADR-007 §8.3: UTF-8, compact separators, keys sorted at
                            every level, non-ASCII unescaped, floats shortest
                            round-trip, NaN / Infinity rejected
    unit_id                 ingress-boundaries.md §6
    recording unit key      ingress-boundaries.md §6, scene-domain.md §6,
                            episode-domain.md §3
    manifest_artifact_id    ingress-boundaries.md §6
    producer fingerprint    ADR-007 §15.2 with the recording source revision of
                            ingress-boundaries.md §4
    recording_artifact_id   ingress-boundaries.md §4
    composition_checksum    data-model.md §2.1
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

UNIT_ID_SCHEMA = "sceneops.unit_id/v2"
RECORDING_SEGMENTATION_SCHEMA = "sceneops.recording_segmentation/v1"
MANIFEST_ARTIFACT_ID_SCHEMA = "sceneops.unit_manifest_artifact_id/v1"
FINGERPRINT_SCHEMA = "sceneops.producer_fingerprint/v1"
COMPOSITION_SCHEMA = "sceneops.dataset_version_composition/v1"

DOMAINS = ("scene", "episode")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum(data: bytes) -> str:
    """A checksum as SceneOps spells it: ``sha256:<hex>``."""
    return "sha256:" + sha256_hex(data)


def unit_id(*, domain: str, source_kind: str, source_id: str, unit_key: str) -> str:
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain {domain!r}")
    document = {
        "unit_id_schema": UNIT_ID_SCHEMA,
        "domain": domain,
        "source_kind": source_kind,
        "source_id": source_id,
        "unit_key": unit_key,
    }
    return f"{domain}-{sha256_hex(canonical_json(document))[:32]}"


def recording_unit_key(*, boundary: Mapping[str, Any], segment: str) -> str:
    """``<policy>-<sha256(boundary document)[:16]>/<segment>``; the boundary
    document carries its schema identifier beside the boundary members."""
    document = {
        "recording_segmentation_schema": RECORDING_SEGMENTATION_SCHEMA,
        **boundary,
    }
    return f"{boundary['policy']}-{sha256_hex(canonical_json(document))[:16]}/{segment}"


def manifest_artifact_id(*, unit_id: str, manifest_checksum: str) -> str:
    domain = unit_id.split("-", 1)[0]
    document = {
        "manifest_artifact_id_schema": MANIFEST_ARTIFACT_ID_SCHEMA,
        "unit_id": unit_id,
        "manifest_checksum": manifest_checksum,
    }
    return f"{domain}-manifest-{sha256_hex(canonical_json(document))[:32]}"


def recording_source_revision(
    *, robot_run_id: str, recording_checksum: str
) -> dict[str, str]:
    return {
        "source_kind": "recording",
        "robot_run_id": robot_run_id,
        "recording_checksum": recording_checksum,
    }


def producer_fingerprint(
    *,
    producer_id: str,
    semantics_version: int,
    build_config: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str:
    document = {
        "fingerprint_schema": FINGERPRINT_SCHEMA,
        "producer_id": producer_id,
        "semantics_version": semantics_version,
        "build_config": build_config,
        "source": source,
    }
    return checksum(canonical_json(document))


def recording_artifact_id(robot_run_id: str) -> str:
    return f"art-robotrun-{robot_run_id}"


def composition_checksum(pins: Iterable[tuple[str, str, str]]) -> str:
    """Over the set of ``(domain, unit_id, manifest_checksum)`` pins."""
    members = sorted([list(pin) for pin in pins])
    return checksum(
        canonical_json({"composition_schema": COMPOSITION_SCHEMA, "members": members})
    )


def digest(items: Iterable[Any]) -> str:
    """A checksum over an ordered list of JSON values: how an expectation
    pins a long sequence (timestamps, payload checksums) in a small file."""
    return checksum(canonical_json(list(items)))
