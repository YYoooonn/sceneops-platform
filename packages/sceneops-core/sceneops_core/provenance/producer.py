"""Producer provenance and the producer fingerprint (ADR-007 §15, §27.7).

``ProducerInfo`` records *how* a canonical unit was constructed: which
producer, under which semantics version, with which semantic build
configuration. It never records the execution that happened to run it (job
id, pipeline run id, timestamps, hosts, temporary paths).

Fingerprint definition (frozen)::

    producer_fingerprint = "sha256:" + hex(sha256(canonical_json({
        "fingerprint_schema": "sceneops.producer_fingerprint/v1",
        "producer_id":        producer_id,
        "semantics_version":  semantics_version,
        "build_config":       normalized build_config,
        "source":             RecordingSourceRevision (JSON),
    })))

Equal fingerprints mean semantically equivalent construction: same source
revision, same producer, same semantics version, same normalized semantic
configuration. The output manifest checksum is never an input (that would
be circular), so the fingerprint is known before building.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.identifiers import validate_producer_id

from .sources import SHA256_CHECKSUM_PATTERN, RecordingSourceRevision

PRODUCER_FINGERPRINT_SCHEMA_V1: Final = "sceneops.producer_fingerprint/v1"

# Execution-scoped keys that can never be semantic build configuration. A
# defensive check only -- producers own normalization -- but it turns the
# most likely mistake (passing job/execution context through) into a loud
# failure instead of a fingerprint that never matches a retry.
EXECUTION_SCOPED_CONFIG_KEYS: Final = frozenset(
    {
        "job_id",
        "pipeline_run_id",
        "pipeline_task_run_id",
        "execution_id",
        "generated_at",
        "created_at",
        "hostname",
        "worker_hostname",
    }
)


def _check_plain_json(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"build_config keys must be strings, got {key!r} at {path or '<root>'}"
                )
            _check_plain_json(item, f"{path}{key}.")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_plain_json(item, f"{path}[{index}].")
    elif value is not None and not isinstance(value, (str, int, float)):
        hint = (
            "; represent an unordered collection as a sorted list"
            if isinstance(value, (set, frozenset))
            else ""
        )
        raise ValueError(
            f"build_config value at {path or '<root>'} is not a plain JSON "
            f"value: {type(value).__name__}{hint}"
        )


def _reject_execution_keys(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in EXECUTION_SCOPED_CONFIG_KEYS:
                raise ValueError(
                    f"build_config must not contain execution-scoped key {path}{key!r}"
                )
            _reject_execution_keys(item, f"{path}{key}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_execution_keys(item, f"{path}[{index}].")


def normalize_build_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the normalized, plain-JSON form of a semantic build config.

    Normalization here is mechanical: the value must be a JSON object of
    plain JSON values, finite floats only, and it is round-tripped through
    the canonical serializer (enums become their values, tuples become
    lists). Sets are rejected because their iteration order is not
    deterministic; represent an unordered collection, such as a selected
    channel set, as a sorted list.

    Semantic normalization is the producer's responsibility and is part of
    its contract: make defaults explicit, sort semantically unordered
    collections, and omit anything that does not affect output semantics.
    """
    if not isinstance(config, Mapping):
        raise ValueError(
            f"build_config must be a JSON object, got {type(config).__name__}"
        )
    config = dict(config)
    _check_plain_json(config, "")
    try:
        normalized = json.loads(canonical_json_bytes(config))
    except ValueError as exc:
        raise ValueError(
            f"build_config is not canonical-JSON serializable: {exc}"
        ) from exc
    _reject_execution_keys(normalized, "")
    return normalized


def compute_producer_fingerprint(
    *,
    producer_id: str,
    semantics_version: int,
    build_config: Mapping[str, Any],
    source: RecordingSourceRevision,
) -> str:
    source_revision = RecordingSourceRevision.model_validate(source)
    payload = {
        "fingerprint_schema": PRODUCER_FINGERPRINT_SCHEMA_V1,
        "producer_id": validate_producer_id(producer_id),
        "semantics_version": _validate_semantics_version(semantics_version),
        "build_config": normalize_build_config(build_config),
        "source": source_revision.model_dump(mode="json"),
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _validate_semantics_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"semantics_version must be an int >= 1, got {value!r}")
    return value


class ProducerFingerprintMismatchError(ValueError):
    """A ProducerInfo's fingerprint does not match its declared inputs."""


class ProducerInfo(BaseModel):
    """How a canonical unit was constructed.

    ``semantics_version`` must be bumped whenever unchanged source and
    configuration could produce semantically different output (§15.3).
    Build ProducerInfo with :meth:`create`, which computes the fingerprint;
    a parsed ProducerInfo is checked against its unit's source with
    :meth:`verify`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    producer_id: StrictStr
    semantics_version: StrictInt = Field(ge=1)
    build_config: dict[str, Any]
    producer_fingerprint: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    @field_validator("producer_id")
    @classmethod
    def _check_producer_id(cls, value: str) -> str:
        return validate_producer_id(value)

    @field_validator("build_config")
    @classmethod
    def _normalize_build_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_build_config(value)

    @classmethod
    def create(
        cls,
        *,
        producer_id: str,
        semantics_version: int,
        build_config: Mapping[str, Any],
        source: RecordingSourceRevision,
    ) -> ProducerInfo:
        return cls(
            producer_id=producer_id,
            semantics_version=semantics_version,
            build_config=dict(build_config),
            producer_fingerprint=compute_producer_fingerprint(
                producer_id=producer_id,
                semantics_version=semantics_version,
                build_config=build_config,
                source=source,
            ),
        )

    def verify(self, source: RecordingSourceRevision) -> None:
        expected = compute_producer_fingerprint(
            producer_id=self.producer_id,
            semantics_version=self.semantics_version,
            build_config=self.build_config,
            source=source,
        )
        if expected != self.producer_fingerprint:
            raise ProducerFingerprintMismatchError(
                f"producer_fingerprint {self.producer_fingerprint} does not "
                f"match its producer, build_config and source revision "
                f"(expected {expected})"
            )


__all__ = [
    "EXECUTION_SCOPED_CONFIG_KEYS",
    "PRODUCER_FINGERPRINT_SCHEMA_V1",
    "ProducerFingerprintMismatchError",
    "ProducerInfo",
    "compute_producer_fingerprint",
    "normalize_build_config",
]
