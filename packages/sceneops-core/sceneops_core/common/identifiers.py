"""Open, stable identifiers for values that become part of persisted
identity or fingerprints (ADR-007 §27.6).

External format identifiers (``ExternalDatasetRef.format``), source clock
identifiers and producer identifiers are open strings, not closed core
enums: a new integration, clock or producer adds a value, not a schema
change. Because they participate in canonical identity, the stored value
must already be canonical. They are validated, never normalized: lowercasing
or rewriting a persisted identifier silently would change identity.
Accepting user-facing aliases ("NuScenes", "nu-scenes") is the integration
boundary's job, which resolves them to the canonical identifier before it
reaches a contract.

Renaming a canonical identifier later is an identity-affecting migration.
"""

from __future__ import annotations

import re
from typing import Final

OPEN_IDENTIFIER_PATTERN: Final = r"^[a-z0-9][a-z0-9._-]*$"
OPEN_IDENTIFIER_MAX_LENGTH: Final = 64
PRODUCER_ID_MAX_LENGTH: Final = 128
VERBATIM_KEY_MAX_LENGTH: Final = 256
LOCAL_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
LOCAL_ID_MAX_LENGTH: Final = 128

_OPEN_IDENTIFIER_RE = re.compile(OPEN_IDENTIFIER_PATTERN)
_LOCAL_ID_RE = re.compile(LOCAL_ID_PATTERN)


def validate_open_identifier(
    value: str, *, field: str, max_length: int = OPEN_IDENTIFIER_MAX_LENGTH
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if len(value) > max_length or not _OPEN_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be a canonical identifier of 1-{max_length} "
            f"characters matching [a-z0-9][a-z0-9._-]*, got {value!r}"
        )
    return value


def validate_external_format(value: str) -> str:
    """Canonical external format identifier, e.g. ``nuscenes``, ``lerobot``,
    ``waymo-open-dataset``. Selects an integration; it is not a SceneOps
    domain type. The format's own version is a separate field."""
    return validate_open_identifier(value, field="external format")


def validate_source_clock(value: str) -> str:
    """Canonical source clock identifier: the clock domain in which a
    source timestamp is an integer count (ADR-007 §27.3).

    SceneOps-defined clocks are unqualified (``mcap_log_time``). A clock
    defined by an external dataset's own timebase is qualified by that
    format's identifier, ``<format>.<clock>``, so integrations never need a
    core change to declare one.
    """
    return validate_open_identifier(value, field="source_clock")


def validate_producer_id(value: str) -> str:
    """Stable producer identity, e.g. ``sceneops.recording_scene_builder``."""
    return validate_open_identifier(
        value, field="producer_id", max_length=PRODUCER_ID_MAX_LENGTH
    )


def validate_local_id(
    value: str, *, field: str, max_length: int = LOCAL_ID_MAX_LENGTH
) -> str:
    """A producer-assigned id that is unique only inside one manifest (an
    observation, calibration, pose, group or annotation id). Path-safe, so
    derived workflows can use it in object keys."""
    if (
        not isinstance(value, str)
        or len(value) > max_length
        or not _LOCAL_ID_RE.fullmatch(value)
    ):
        raise ValueError(
            f"{field} must be 1-{max_length} characters of [A-Za-z0-9._-] "
            f"starting with an alphanumeric character, got {value!r}"
        )
    return value


def validate_verbatim_key(
    value: str, *, field: str, max_length: int = VERBATIM_KEY_MAX_LENGTH
) -> str:
    """A key kept exactly as its source names it (a source unit key, a ROS
    topic, a nuScenes channel, a coordinate frame, an annotation category).

    Its alphabet is open because it belongs to the source, but it must be
    unambiguous: non-empty, bounded, no surrounding whitespace and no
    control characters. It is validated, never normalized.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if not value or len(value) > max_length:
        raise ValueError(f"{field} must be 1-{max_length} characters")
    if value != value.strip():
        raise ValueError(f"{field} must not have surrounding whitespace")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{field} must not contain control characters")
    return value


__all__ = [
    "LOCAL_ID_MAX_LENGTH",
    "LOCAL_ID_PATTERN",
    "OPEN_IDENTIFIER_MAX_LENGTH",
    "OPEN_IDENTIFIER_PATTERN",
    "PRODUCER_ID_MAX_LENGTH",
    "VERBATIM_KEY_MAX_LENGTH",
    "validate_external_format",
    "validate_local_id",
    "validate_open_identifier",
    "validate_producer_id",
    "validate_source_clock",
    "validate_verbatim_key",
]
