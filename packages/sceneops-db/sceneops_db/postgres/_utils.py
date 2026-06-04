from __future__ import annotations

from typing import Any

from sqlalchemy import Select


def enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def apply_pagination(stmt: Select[Any], *, limit: int, offset: int) -> Select[Any]:
    return stmt.limit(limit).offset(offset)


def values_without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def apply_values(model: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(model, key, value)
