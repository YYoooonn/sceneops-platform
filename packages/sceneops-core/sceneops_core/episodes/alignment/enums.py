from __future__ import annotations

from enum import StrEnum


class TimelineMode(StrEnum):
    """Phase 2 v1 supports exactly one timeline mode. Not a permanent
    platform invariant — future modes (event_driven, observation_driven,
    action_driven) extend this enum without redefining Episode itself
    (SceneOps V2 Request 2.1B §4)."""

    FIXED_FREQUENCY = "fixed_frequency"


class AssociationPolicy(StrEnum):
    """v1 association policy vocabulary (SceneOps V2 Request 2.1B §8).
    Deliberately excludes slerp/backfill/extrapolation."""

    EXACT = "exact"
    NEAREST = "nearest"
    PREVIOUS = "previous"
    LINEAR_INTERPOLATION = "linear_interpolation"


class AlignedSignalStatus(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    INTERPOLATED = "interpolated"


class AlignedValueKind(StrEnum):
    """Structural classification of an aligned value's payload shape —
    drives interpolation eligibility (orientation/reference are never
    interpolable in v1) independent of channel-name lookups."""

    NUMERIC_SCALAR = "numeric_scalar"
    NUMERIC_VECTOR = "numeric_vector"
    ORIENTATION = "orientation"
    REFERENCE = "reference"


class ChannelNamespace(StrEnum):
    """Which manifest list a channel comes from — action channels always
    default to hold-last; observation channels have per-channel defaults
    (SceneOps V2 Request 2.1B §9)."""

    OBSERVATION = "observation"
    ACTION = "action"
