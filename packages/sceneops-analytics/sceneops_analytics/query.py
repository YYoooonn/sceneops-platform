from __future__ import annotations

import re

import duckdb
import polars as pl

_VALID_VIEW_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def query_parquet(views: dict[str, str], sql: str) -> pl.DataFrame:
    """Run a DuckDB SQL query against one or more local Parquet files.

    ``views`` maps a view name to a local filesystem path; each becomes a
    DuckDB view over that Parquet file, so ``sql`` can reference it like a
    normal table.

    Local debugging/exploration only, matching the role split in
    docs/architecture/storage-layout.md (PyArrow -> schema/Parquet IO, Polars ->
    filter/join/aggregation, DuckDB -> local SQL). Object-storage-backed
    ArtifactStores (S3/MinIO) aren't supported here — that would need
    DuckDB's own httpfs/S3 extension configured with credentials, which this
    repo doesn't wire up; callers on S3 need to download the Parquet files
    locally first.
    """
    con = duckdb.connect()
    try:
        for name, path in views.items():
            if not _VALID_VIEW_NAME.match(name):
                raise ValueError(f"Invalid view name: {name!r}")
            escaped_path = path.replace("'", "''")
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{escaped_path}')"
            )
        return con.execute(sql).pl()
    finally:
        con.close()


__all__ = ["query_parquet"]
