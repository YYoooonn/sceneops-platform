from __future__ import annotations

from typing import Any

from sqlalchemy import Select


# Upper bound on ids per ``IN (...)`` list, well below the driver's bind
# parameter limit.
IN_CLAUSE_CHUNK = 1000


def enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def apply_pagination(stmt: Select[Any], *, limit: int, offset: int) -> Select[Any]:
    return stmt.limit(limit).offset(offset)


def values_without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


# Columns the database owns: server_default on insert, onupdate on update. An
# in-memory record that never read them back carries None, and writing that
# would violate their NOT NULL constraint on re-saving an existing row.
_DB_MANAGED_TIMESTAMPS = frozenset({"created_at", "updated_at"})


def apply_values(model: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is None and key in _DB_MANAGED_TIMESTAMPS:
            continue
        setattr(model, key, value)
