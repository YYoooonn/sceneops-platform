"""The strict, canonical-bytes read path shared by the source preservation
records (the RobotRunManifest rule, ADR-007 §12.1 R2-R3): bytes are accepted
only if ``canonical(parse(data)) == data``."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

_M = TypeVar("_M", bound=BaseModel)


def load_canonical_model(
    data: bytes,
    model: type[_M],
    *,
    schema_version: str,
    error: type[ValueError],
) -> _M:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error(f"{model.__name__} is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise error(f"{model.__name__} must be a JSON object")
    if payload.get("schema_version") != schema_version:
        raise error(
            f"unsupported {model.__name__} schema_version: "
            f"{payload.get('schema_version')!r}"
        )
    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:
        raise error(f"invalid {model.__name__}: {exc}") from exc
    if parsed.to_canonical_bytes() != data:  # type: ignore[attr-defined]
        raise error(f"{model.__name__} bytes are not in canonical form")
    return parsed
