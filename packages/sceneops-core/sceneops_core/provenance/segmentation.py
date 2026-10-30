"""Recording unit keys: the segmentation policy is part of the key
(ADR-015 §6.3)::

    unit_key = <policy>-<sha256(canonical_json({
        "recording_segmentation_schema": "sceneops.recording_segmentation/v1",
        **boundary                     the segmentation's boundary document
    }))[:16]>/<segment>

A recording has no native unit key: a builder cuts it into units. The same
segment index under two segmentations names two different units (other
windows, other boundaries), so the key carries the segmentation. It carries
exactly the configuration that decides where unit boundaries fall (policy,
clock, duration, marker source and rule), never what is inside a unit
(channels, streams, payloads, frames): changing those is a new revision of
the same units, while changing a boundary rule yields new units.

``<segment>`` names one unit within its segmentation and is decided by the
source and the boundary document alone, never by the selected channels:

    whole_recording   recording
    fixed_duration    <k>: the window [k * duration_ns, (k + 1) * duration_ns)
                      on the segmentation clock, a grid anchored at the
                      clock's zero
    event_markers     task-<key>-<occurrence>

The digest keeps the key bounded whatever the boundary document holds; the
unit id hashes the whole key, so a collision of two segmentations of one
RobotRun needs a 64-bit prefix collision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Final

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.identifiers import (
    validate_open_identifier,
    validate_verbatim_key,
)

RECORDING_SEGMENTATION_KEY_SCHEMA_V1: Final = "sceneops.recording_segmentation/v1"
WHOLE_RECORDING_SEGMENT: Final = "recording"
_SEGMENTATION_DIGEST_HEX_LENGTH: Final = 16


def recording_unit_key(*, boundary: Mapping[str, Any], segment: str) -> str:
    """The unit key of one segment of a recording under the segmentation
    whose boundary document is ``boundary`` (its ``policy`` names it)."""
    policy = validate_open_identifier(
        boundary.get("policy"), field="segmentation policy"
    )
    document = {"recording_segmentation_schema": RECORDING_SEGMENTATION_KEY_SCHEMA_V1}
    document.update(boundary)
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    segment = validate_verbatim_key(segment, field="segment key")
    return f"{policy}-{digest[:_SEGMENTATION_DIGEST_HEX_LENGTH]}/{segment}"


def fixed_duration_index(timestamp_ns: int, duration_ns: int) -> int:
    """The grid window a timestamp falls in: ``[k * d, (k + 1) * d)``."""
    if duration_ns < 1:
        raise ValueError(f"duration_ns must be >= 1, got {duration_ns}")
    return timestamp_ns // duration_ns


def fixed_duration_window(index: int, duration_ns: int) -> tuple[int, int]:
    """The half-open bounds of grid window ``index``."""
    if duration_ns < 1:
        raise ValueError(f"duration_ns must be >= 1, got {duration_ns}")
    return index * duration_ns, (index + 1) * duration_ns


__all__ = [
    "RECORDING_SEGMENTATION_KEY_SCHEMA_V1",
    "WHOLE_RECORDING_SEGMENT",
    "fixed_duration_index",
    "fixed_duration_window",
    "recording_unit_key",
]
