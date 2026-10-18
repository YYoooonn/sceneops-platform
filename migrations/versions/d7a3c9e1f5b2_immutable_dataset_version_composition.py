"""Immutable DatasetVersion composition

Revision ID: d7a3c9e1f5b2
Revises: c4e8a2f6d1b9
Create Date: 2026-10-18

ADR-015 §7, MS-8: a DatasetVersion exists only as a complete composition,
written with all of its members in one transaction by
COMPOSE_DATASET_VERSION and never changed afterwards.

dataset_versions
    + composition_checksum   the name's one composition (sha256, CHECK)
    + selector               how the members were chosen; NULL for explicit pins
    - status, updated_at     a committed version has no lifecycle
    summary columns          no server default: the composer computes them;
                             CHECK non-negative and at least one member
    dataset_id               FK datasets ON DELETE RESTRICT (was CASCADE)
    UPDATE                   rejected (sceneops_reject_update)

Existing rows are versions created empty and filled by registrars; they
have no composition, so the upgrade refuses to run while any exists. Reset
development state instead. The downgrade refuses symmetrically.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d7a3c9e1f5b2"
down_revision: str | None = "c4e8a2f6d1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "dataset_versions"
_COUNTS = ("scene_count", "keyframe_count", "observation_count", "episode_count")


def _refuse_if_rows() -> None:
    count = (
        op.get_bind().execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    )
    if count:
        raise RuntimeError(
            f"{_TABLE} holds {count} row(s) that are not immutable compositions; "
            "reset development state"
        )


def _replace_dataset_fk(ondelete: str) -> None:
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(_TABLE):
        if fk["constrained_columns"] == ["dataset_id"]:
            op.drop_constraint(fk["name"], _TABLE, type_="foreignkey")
            break
    else:
        raise RuntimeError(f"{_TABLE} has no foreign key on dataset_id")
    op.create_foreign_key(
        "fk_dataset_versions_dataset_id",
        _TABLE,
        "datasets",
        ["dataset_id"],
        ["dataset_id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _refuse_if_rows()

    op.drop_index("ix_dataset_versions_status", table_name=_TABLE)
    op.drop_column(_TABLE, "status")
    op.drop_column(_TABLE, "updated_at")

    op.add_column(
        _TABLE, sa.Column("composition_checksum", sa.String(71), nullable=False)
    )
    op.add_column(_TABLE, sa.Column("selector", JSONB, nullable=True))
    for column in _COUNTS:
        op.alter_column(_TABLE, column, server_default=None)

    op.create_check_constraint(
        "ck_dataset_versions_composition_checksum",
        _TABLE,
        "composition_checksum ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_dataset_versions_counts",
        _TABLE,
        "scene_count >= 0 AND keyframe_count >= 0 AND observation_count >= 0 "
        "AND episode_count >= 0 AND scene_count + episode_count > 0",
    )
    _replace_dataset_fk("RESTRICT")

    op.execute(
        f"CREATE TRIGGER {_TABLE}_immutable BEFORE UPDATE ON {_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION sceneops_reject_update()"
    )


def downgrade() -> None:
    _refuse_if_rows()

    op.execute(f"DROP TRIGGER {_TABLE}_immutable ON {_TABLE}")
    _replace_dataset_fk("CASCADE")
    op.drop_constraint("ck_dataset_versions_counts", _TABLE, type_="check")
    op.drop_constraint(
        "ck_dataset_versions_composition_checksum", _TABLE, type_="check"
    )

    for column in _COUNTS:
        op.alter_column(_TABLE, column, server_default=sa.text("0"))
    op.drop_column(_TABLE, "selector")
    op.drop_column(_TABLE, "composition_checksum")

    op.add_column(
        _TABLE,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'registered'"),
        ),
    )
    op.create_index("ix_dataset_versions_status", _TABLE, ["status"])
