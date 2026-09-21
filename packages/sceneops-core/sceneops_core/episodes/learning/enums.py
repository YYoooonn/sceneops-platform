from __future__ import annotations

from enum import StrEnum


class MissingFeaturePolicy(StrEnum):
    """How dense projection handles a required feature whose signal has
    status=missing at a given step (SceneOps V2 Request 2.7A §9). v1
    defines exactly one member -- ERROR -- no zero-fill/forward-fill/
    NaN-fill/drop policy exists yet. Kept as an extensible enum (rather
    than a bare constant) so a future masking/drop policy can be added
    without changing project_step/project_sequence's signature."""

    ERROR = "error"
