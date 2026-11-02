from __future__ import annotations

import re
from collections.abc import Mapping

import duckdb
import polars as pl
import pyarrow.parquet as pq

from .tables import check_scene_table_files, is_scene_table_file

_VALID_VIEW_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _run_sql(views: Mapping[str, str], sql: str) -> pl.DataFrame:
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


def query_parquet(views: dict[str, str], sql: str) -> pl.DataFrame:
    """Run a DuckDB SQL query against one or more local Parquet files.

    ``views`` maps a view name to a local filesystem path; each becomes a
    DuckDB view over that Parquet file, so ``sql`` can reference it like a
    normal table.

    It declares no table contract, so it checks none, and it refuses a Scene
    analytics table of any version (``query_scene_analytics`` reads those).

    Local debugging/exploration only, matching the role split in
    docs/architecture/storage-layout.md (PyArrow -> schema/Parquet IO, Polars ->
    filter/join/aggregation, DuckDB -> local SQL). Object-storage-backed
    ArtifactStores (S3/MinIO) aren't supported here — that would need
    DuckDB's own httpfs/S3 extension configured with credentials, which this
    repo doesn't wire up; callers on S3 need to download the Parquet files
    locally first.
    """
    for name, path in views.items():
        if is_scene_table_file(pq.read_metadata(path)):
            raise ValueError(
                f"view {name!r}: {path} is a Scene analytics table; read it with "
                "query_scene_analytics, which checks its contract"
            )
    return _run_sql(views, sql)


def query_scene_analytics(tables: Mapping[str, str], sql: str) -> pl.DataFrame:
    """Run a DuckDB SQL query over local Scene analytics tables of one snapshot.

    ``tables`` maps a table name (``scenes``, ``observations``, ``keyframes``) to a
    local path, and each is a view of that name. Validation is eager: every file's
    footer, every row's clock instance and the tables' agreement on them are proven
    before the query runs, so a refused file returns no rows. Local files only, as
    for ``query_parquet``.

    SQL compares what the caller writes: a join on ``(source_clock, timestamp_ns)``
    alone still matches unrelated clocks. Comparable timestamps have equal
    ``source_clock``, ``clock_origin`` and ``clock_scope``.
    """
    check_scene_table_files(tables)
    return _run_sql(tables, sql)


__all__ = ["query_parquet", "query_scene_analytics"]
