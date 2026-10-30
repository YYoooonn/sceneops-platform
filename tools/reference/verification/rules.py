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
    Logical URI             ADR-016: sceneops://<namespace>/<key>, each key segment
                            percent-encoded over its UTF-8 bytes, every byte outside
                            A-Z a-z 0-9 - . _ ~ as uppercase %XX; one spelling per
                            key
    payload keys            storage-layout.md: a recording payload at
                            <robot_run_id>/<payload artifact id> in
                            observation-payloads, a preserved file at
                            contents/<content_id>/files/<path> in sources
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

OBSERVATION_PAYLOADS = "observation-payloads"
SOURCES = "sources"
# The fields of a payload reference (ADR-017): Logical URI, full-object
# integrity, media type and an optional selector; nothing else.
PAYLOAD_REF_FIELDS = frozenset({"uri", "checksum", "size_bytes", "media_type", "selector"})
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_HEX = frozenset("0123456789ABCDEF")

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


def _encode_segment(segment: str) -> str:
    return "".join(
        chr(b) if chr(b) in _UNRESERVED else f"%{b:02X}"
        for b in segment.encode("utf-8")
    )


def _decode_segment(encoded: str) -> str | None:
    out = bytearray()
    i = 0
    while i < len(encoded):
        if encoded[i] == "%":
            pair = encoded[i + 1 : i + 3]
            if len(pair) != 2 or not set(pair) <= _HEX:
                return None
            out.append(int(pair, 16))
            i += 3
        elif encoded[i] in _UNRESERVED:
            out.append(ord(encoded[i]))
            i += 1
        else:
            return None
    try:
        segment = out.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "/" in segment or _encode_segment(segment) != encoded:
        return None
    return segment


def logical_uri(namespace: str, key: str) -> str:
    """The one spelling of ``key`` in ``namespace``."""
    segments = key.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise ValueError(f"{key!r} is not an object key")
    return f"sceneops://{namespace}/" + "/".join(_encode_segment(s) for s in segments)


def logical_key(uri: Any, namespace: str) -> str | None:
    """The key a canonical Logical URI in ``namespace`` names, or None when
    ``uri`` is anything else (another namespace, another spelling)."""
    prefix = f"sceneops://{namespace}/"
    if not isinstance(uri, str) or not uri.startswith(prefix):
        return None
    segments = [_decode_segment(s) for s in uri[len(prefix) :].split("/")]
    if any(s is None or s in ("", ".", "..") for s in segments):
        return None
    return "/".join(segments)  # type: ignore[arg-type]


def observation_payload_uri(robot_run_id: str, payload_artifact_id: str) -> str:
    return logical_uri(OBSERVATION_PAYLOADS, f"{robot_run_id}/{payload_artifact_id}")


def content_file_uri(content_id: str, path: str) -> str:
    return logical_uri(SOURCES, f"contents/{content_id}/files/{path}")


def payload_artifact_id_of(ref: Any) -> str | None:
    """The payload artifact id a recording payload reference's URI names
    (``<robot_run_id>/<id>``), or None when it names no such key."""
    key = logical_key((ref or {}).get("uri"), OBSERVATION_PAYLOADS)
    if key is None or key.count("/") != 1:
        return None
    return key.partition("/")[2]


def content_file_path_of(ref: Any, content_id: str) -> str | None:
    """The inventory path a payload reference's URI names as a preserved file
    of ``content_id``, or None when it names no such file."""
    key = logical_key((ref or {}).get("uri"), SOURCES)
    prefix = f"contents/{content_id}/files/"
    if key is None or not key.startswith(prefix):
        return None
    return key[len(prefix) :]
