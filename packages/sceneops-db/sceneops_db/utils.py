from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sceneops_core.common.schemas import ErrorInfo


def extract_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value

    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return None


def enum_to_str(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, Enum):
        return value.value

    return str(value)


def to_error_info(value: Any) -> ErrorInfo | None:
    if value is None:
        return None

    if isinstance(value, ErrorInfo):
        return value

    if isinstance(value, str):
        return ErrorInfo(type="UnknownError", message=value)

    if isinstance(value, dict):
        message = value.get("message")
        details = value.get("details")

        return ErrorInfo(
            type=str(value.get("type") or "UnknownError"),
            message=str(message if message is not None else value),
            details=details if details is not None else {},
        )

    return ErrorInfo(
        type=value.__class__.__name__,
        message=str(value),
    )


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}

    if isinstance(value, list):
        return [to_jsonable(item) for item in value]

    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]

    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=str)]

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


def to_error_json(value: Any) -> dict[str, Any] | None:
    error = to_error_info(value)
    return error.model_dump(mode="json") if error is not None else None
