"""Field path vocabulary of the Episode contract: how a selected value is
addressed inside a source message or dataset feature. A path is dotted
identifiers, kept verbatim; every build configuration and the EpisodeManifest
use the same rule."""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import AfterValidator, StrictStr

FIELD_PATH_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
_FIELD_PATH_RE = re.compile(FIELD_PATH_PATTERN)


def _validate_field_path(value: str) -> str:
    if not isinstance(value, str) or not _FIELD_PATH_RE.fullmatch(value):
        raise ValueError(
            f"field path must be dotted identifiers ({FIELD_PATH_PATTERN}), got {value!r}"
        )
    return value


FieldPath = Annotated[StrictStr, AfterValidator(_validate_field_path)]

__all__ = ["FIELD_PATH_PATTERN", "FieldPath"]
