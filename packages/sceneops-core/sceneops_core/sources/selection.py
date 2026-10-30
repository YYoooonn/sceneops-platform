"""The include-pattern dialect of a SourceSnapshot selection (ADR-015 §5, D1).

A pattern matches a whole file path relative to the source root:

    *     any run of characters within one segment (including none and a
          leading '.')
    ?     exactly one character within one segment
    **    a whole segment that matches zero or more segments
    other characters match themselves, case-sensitively

``[ ] { } !`` and ``\\`` are rejected rather than given a meaning, and ``**``
inside a longer segment (``a**``) is rejected, so every accepted pattern has
exactly one reading. The dialect is part of the meaning of
``selection_checksum`` and therefore of ``snapshot_id`` (``snapshot_id_schema``
v1): changing what a pattern matches requires a new schema.

Selection fails instead of preserving less: a pattern that matches no
candidate path is an error, so a typo cannot silently drop files.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Final

from sceneops_core.common.identifiers import validate_verbatim_key

INCLUDE_PATTERN_MAX_LENGTH: Final = 1024
_RESERVED_CHARACTERS: Final = frozenset("[]{}!\\")
_RECURSIVE_SEGMENT: Final = "**"


class SourceSelectionError(ValueError):
    """A selection does not select a well-defined, non-empty set of files."""


def validate_include_pattern(value: str) -> str:
    validate_verbatim_key(
        value, field="include pattern", max_length=INCLUDE_PATTERN_MAX_LENGTH
    )
    if value.startswith("/"):
        raise ValueError(
            f"include pattern must be relative to the source root, got {value!r}"
        )
    reserved = sorted(_RESERVED_CHARACTERS.intersection(value))
    if reserved:
        raise ValueError(
            f"include pattern {value!r} uses reserved characters {reserved}; only "
            "'*', '?' and whole-segment '**' are wildcards"
        )
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            raise ValueError(
                f"include pattern must not contain empty, '.' or '..' segments, "
                f"got {value!r}"
            )
        if _RECURSIVE_SEGMENT in segment and segment != _RECURSIVE_SEGMENT:
            raise ValueError(
                f"'**' must be a whole segment in include pattern {value!r}"
            )
    return value


@lru_cache(maxsize=256)
def _segment_regex(segment: str) -> re.Pattern[str]:
    parts = []
    for character in segment:
        if character == "*":
            parts.append("[^/]*")
        elif character == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(character))
    # DOTALL: a candidate path with a control character must still be matched,
    # so that path validation rejects it instead of selection skipping it.
    return re.compile("".join(parts), re.DOTALL)


def _match_segments(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == _RECURSIVE_SEGMENT:
        return any(_match_segments(rest, path[skip:]) for skip in range(len(path) + 1))
    if not path or _segment_regex(head).fullmatch(path[0]) is None:
        return False
    return _match_segments(rest, path[1:])


def include_pattern_matches(pattern: str, path: str) -> bool:
    """Whether ``pattern`` (already validated) matches the whole ``path``."""
    return _match_segments(tuple(pattern.split("/")), tuple(path.split("/")))


def select_paths(patterns: Iterable[str], candidates: Iterable[str]) -> tuple[str, ...]:
    """The candidates matched by at least one pattern, sorted and unique.

    Raises ``SourceSelectionError`` naming every pattern that matches no
    candidate. Candidate paths are not validated here; the inventory rejects
    a selected path that has more than one spelling."""
    patterns = tuple(patterns)
    paths = sorted(set(candidates))
    selected: set[str] = set()
    unmatched: list[str] = []
    for pattern in patterns:
        hits = [path for path in paths if include_pattern_matches(pattern, path)]
        if not hits:
            unmatched.append(pattern)
        selected.update(hits)
    if unmatched:
        raise SourceSelectionError(
            f"include patterns match no file: {unmatched}; a selection never "
            "silently preserves less than it declares"
        )
    return tuple(sorted(selected))


__all__ = [
    "INCLUDE_PATTERN_MAX_LENGTH",
    "SourceSelectionError",
    "include_pattern_matches",
    "select_paths",
    "validate_include_pattern",
]
