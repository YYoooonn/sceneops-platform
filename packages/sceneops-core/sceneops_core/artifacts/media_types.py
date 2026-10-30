"""Media types of payload bytes (ADR-015 §6.5).

A ``media_type`` is an open, lowercase ``type/subtype`` identifier (RFC 6838
shape, no parameters) that must fully specify the payload's encoding and
layout, e.g. ``image/jpeg`` or ``application/x.nuscenes.lidar-pcd-bin``.
Consumers dispatch on it instead of assuming a source's file layout. Like
other open identifiers it is validated, never normalized.
"""

from __future__ import annotations

import re
from typing import Final

MEDIA_TYPE_PATTERN: Final = (
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)
_MEDIA_TYPE_RE = re.compile(MEDIA_TYPE_PATTERN)


def validate_media_type(value: str) -> str:
    if not isinstance(value, str) or not _MEDIA_TYPE_RE.fullmatch(value):
        raise ValueError(
            f"media_type must be a lowercase 'type/subtype' without parameters, "
            f"got {value!r}"
        )
    return value


def media_top_level_type(media_type: str) -> str:
    """The top-level type of a valid media type (``video`` of ``video/mp4``)."""
    return validate_media_type(media_type).partition("/")[0]


__all__ = ["MEDIA_TYPE_PATTERN", "media_top_level_type", "validate_media_type"]
