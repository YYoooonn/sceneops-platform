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


@pytest.mark.asyncio
async def test_database_has_no_column_the_models_dropped():
    """The reverse of the check above: a migration that was meant to drop a
    column really did (no leftover state the ORM can neither read nor write)."""
    engine = get_async_engine()

    def _reflect(sync_conn):
        insp = inspect(sync_conn)
        return {
            table: {col["name"] for col in insp.get_columns(table)}
            for table in insp.get_table_names()
        }

    async with engine.connect() as conn:
        db_columns = await conn.run_sync(_reflect)

    stale = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_columns:
            continue
        extra = db_columns[table_name] - {c.name for c in table.columns}
        if extra:
            stale.append(
                f"{table_name}: database has columns no model declares: {extra}"
            )

    assert not stale, "\n".join(stale)


def _ondelete(rule: str | None) -> str:
    # The database reports no rule for the default (NO ACTION).
    return (rule or "NO ACTION").upper()


@pytest.mark.asyncio
async def test_every_model_foreign_key_matches_database_delete_rule():
    """ON DELETE is part of the data-integrity contract (e.g. RESTRICT keeps
    immutable RobotRun provenance from being cascaded away), so a migration
    and the model must agree on it, not only on column existence."""
    engine = get_async_engine()

    def _reflect(sync_conn):
        insp = inspect(sync_conn)
        return {
            table: {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                ): _ondelete(fk.get("options", {}).get("ondelete"))
                for fk in insp.get_foreign_keys(table)
            }
            for table in insp.get_table_names()
        }

    async with engine.connect() as conn:
        db_fks = await conn.run_sync(_reflect)

    mismatches = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_fks:
            continue  # already reported by test_every_model_table_exists_in_database
        for constraint in table.foreign_key_constraints:
            key = (
                tuple(c.name for c in constraint.columns),
                constraint.referred_table.name,
                tuple(e.column.name for e in constraint.elements),
            )
            model_rule = _ondelete(constraint.ondelete)
            db_rule = db_fks[table_name].get(key)
            if db_rule is None:
                mismatches.append(f"{table_name}{key}: FK missing in DB")
            elif db_rule != model_rule:
                mismatches.append(
                    f"{table_name}{key}: model ON DELETE {model_rule}, "
                    f"DB ON DELETE {db_rule}"
                )

    assert not mismatches, "\n".join(mismatches)
