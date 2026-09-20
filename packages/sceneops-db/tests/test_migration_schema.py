"""Confirms the live (already-migrated) Postgres schema matches the current
SQLAlchemy models — i.e. the Alembic chain actually produces what the ORM
expects, not just that `alembic upgrade head` exits 0. This is exactly the
kind of divergence that previously only surfaced during E2E runs."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

import sceneops_db.models  # noqa: F401 - populates Base.metadata
from sceneops_db.base import Base
from sceneops_db.session import get_async_engine


@pytest.mark.asyncio
async def test_every_model_table_exists_in_database():
    engine = get_async_engine()
    async with engine.connect() as conn:
        db_tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )

    model_tables = set(Base.metadata.tables.keys())
    missing = model_tables - db_tables
    assert (
        not missing
    ), f"Tables declared in models but missing in the database: {missing}"


@pytest.mark.asyncio
async def test_every_model_column_exists_in_database():
    engine = get_async_engine()

    def _reflect(sync_conn):
        insp = inspect(sync_conn)
        return {
            table: {col["name"] for col in insp.get_columns(table)}
            for table in insp.get_table_names()
        }

    async with engine.connect() as conn:
        db_columns = await conn.run_sync(_reflect)

    mismatches = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_columns:
            continue  # already reported by test_every_model_table_exists_in_database
        model_columns = {c.name for c in table.columns}
        missing = model_columns - db_columns[table_name]
        if missing:
            mismatches.append(
                f"{table_name}: model declares columns missing in DB: {missing}"
            )

    assert not mismatches, "\n".join(mismatches)
