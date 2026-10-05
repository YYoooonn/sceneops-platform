"""Closed vocabularies that SceneManifest v1 defines.

They are canonical semantics that SceneOps owns and versions through the
manifest ``schema_version``; source vocabulary (channel names, frame names,
annotation categories) stays verbatim in the manifest and never becomes an
enum here (ADR-007 §13.8, §20.4).
"""

from __future__ import annotations

from enum import StrEnum


class SceneModality(StrEnum):
    """Canonical modality of a source channel. ``other`` keeps a channel the
    producer includes but does not classify; it is never dropped."""

    CAMERA = "camera"
    LIDAR = "lidar"
    RADAR = "radar"
    OTHER = "other"


class SceneFrameRole(StrEnum):
    """Canonical role of a source coordinate frame, so source-independent
    consumers can find "the ego frame" without knowing that one source calls
    it ``ego`` and another ``base_link``."""

    WORLD = "world"
    EGO = "ego"
    SENSOR = "sensor"


class SceneGroupKind(StrEnum):
    """A grouping of observations that the source itself defines.

    ``keyframe``: a source-designated reference sample over several
    channels (e.g. a hardware-triggered capture set). It indexes
    observations that stay independently represented with their own
    timestamps; it never replaces them, and observations outside any
    keyframe remain canonical.
    """

    KEYFRAME = "keyframe"
