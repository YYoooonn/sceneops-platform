"""How a Parquet table states and proves the contract it was written under.

A table family owns its version, its stamp and its columns. This is the part they
share: where the stamp lives and what comparing columns means.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

TABLE_SCHEMA_METADATA_KEY = "sceneops.table_schema"


def stated_table_schema(footer: pq.FileMetaData) -> bytes | None:
    """What a file's footer states under ``TABLE_SCHEMA_METADATA_KEY``, verbatim."""
    return (footer.metadata or {}).get(TABLE_SCHEMA_METADATA_KEY.encode())


def _arrow_type_contract(data_type: pa.DataType) -> str:
    """An Arrow type as a column contract means it. Which Arrow string or list
    width a writer chose, and what it named a list's child field, are not part of
    the contract: Polars and PyArrow differ on those for the same column."""
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        return "string"
    if pa.types.is_list(data_type) or pa.types.is_large_list(data_type):
        return f"list<{_arrow_type_contract(data_type.value_type)}>"
    return str(data_type)


def arrow_columns(schema: pa.Schema) -> list[tuple[str, str]]:
    """``(column, Arrow type)`` of ``schema``, in column order."""
    return [(field.name, _arrow_type_contract(field.type)) for field in schema]


def declared_columns(schema: dict[str, pl.PolarsDataType]) -> list[tuple[str, str]]:
    """``(column, Arrow type)`` of a table family's declared Polars schema."""
    return arrow_columns(pl.DataFrame(schema=schema).to_arrow().schema)


__all__ = [
    "TABLE_SCHEMA_METADATA_KEY",
    "arrow_columns",
    "declared_columns",
    "stated_table_schema",
]
