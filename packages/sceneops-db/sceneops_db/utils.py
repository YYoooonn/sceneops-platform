from datetime import datetime
from typing import Any

from sceneops_core.schemas.common import ErrorInfo


def extract_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value

    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return None


def enum_to_str(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


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


def to_error_json(value: Any) -> dict[str, Any] | None:
    error = to_error_info(value)
    return error.model_dump(mode="json") if error is not None else None
