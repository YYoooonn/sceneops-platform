"""Separate canonical units, revisions and DatasetVersion membership

Revision ID: c4e8a2f6d1b9
Revises: a9d3f5c1e7b2
Create Date: 2026-10-16

ADR-015 §3, §6.4, §7: a Scene / Episode unit is identified by its source
alone, has one or more immutable manifest revisions, and is a member of a
DatasetVersion only through a pin of one revision.

scenes / episodes
    the unit: id, source_kind ('recording'), robot_run_id (FK robot_runs,
    RESTRICT), unit_key, registered_at; unique (robot_run_id, unit_key).
    Gone: dataset_id, dataset_version, every revision column, updated_at.
scene_revisions / episode_revisions
    one row per registered manifest: PK (unit id, manifest_checksum),
    manifest_artifact_id (FK artifacts, RESTRICT, unique), producer
    fingerprint (unique per unit), window and projection columns;
    unique (unit id, manifest_artifact_id, manifest_checksum) is the pin
    every reference targets.
dataset_version_members
    (dataset_version_id, unit_id) PK, domain, the revision pin, with a
    RESTRICT foreign key to exactly one revision table.
scene_run_records / episode_run_records
    a per-unit row's pin references the revision (RESTRICT) instead of only
    the manifest artifact.

Units, revisions and members reject UPDATE (sceneops_reject_update): they
are written once and never changed.

Unit ids, unit keys and manifest keys all change, so existing rows cannot
be carried over: the upgrade refuses to run while any Scene, Episode or
run record exists. Reset development state instead. The downgrade refuses
symmetrically.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c4e8a2f6d1b9"
down_revision: str | None = "a9d3f5c1e7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_LIST = sa.text("'[]'::jsonb")
_NOW = sa.text("now()")
_SHA256 = "^sha256:[0-9a-f]{64}$"
_IMMUTABLE_TABLES = (
    "scenes",
    "scene_revisions",
    "episodes",
    "episode_revisions",
    "dataset_version_members",
)


def _refuse_if_rows(*tables: str) -> None:
    bind = op.get_bind()
    for table in tables:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"{table} holds {count} row(s) whose identity cannot be converted; "
                "reset development state"
            )


def _drop_fk_on(table: str, columns: list[str]) -> None:
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == columns:
            op.drop_constraint(fk["name"], table, type_="foreignkey")
            return
    raise RuntimeError(f"{table} has no foreign key on {columns}")


def _unit_table(table: str, id_column: str) -> None:
    op.create_table(
        table,
        sa.Column(id_column, sa.String(128), primary_key=True),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column(
            "robot_run_id",
            sa.String(128),
            sa.ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("unit_key", sa.String(256), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.UniqueConstraint("robot_run_id", "unit_key", name=f"uq_{table}_source_unit"),
        sa.CheckConstraint("source_kind = 'recording'", name=f"ck_{table}_source_kind"),
    )


def _revision_columns(unit_table: str, id_column: str) -> list[sa.Column]:
    return [
        sa.Column(
            id_column,
            sa.String(128),
            sa.ForeignKey(f"{unit_table}.{id_column}", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("manifest_checksum", sa.String(71), primary_key=True),
        sa.Column(
            "manifest_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("producer_fingerprint", sa.String(71), nullable=False),
        sa.Column("window_clock", sa.String(64), nullable=False),
        sa.Column("window_start_timestamp_ns", sa.BigInteger(), nullable=False),
        sa.Column("window_end_timestamp_ns", sa.BigInteger(), nullable=False),
    ]


def _revision_constraints(table: str, id_column: str, counts: str) -> list:
    return [
        sa.UniqueConstraint(
            id_column, "producer_fingerprint", name=f"uq_{table}_fingerprint"
        ),
        sa.UniqueConstraint(
            "manifest_artifact_id", name=f"uq_{table}_manifest_artifact_id"
        ),
        sa.UniqueConstraint(
            id_column,
            "manifest_artifact_id",
            "manifest_checksum",
            name=f"uq_{table}_pin",
        ),
        sa.CheckConstraint(
            "window_end_timestamp_ns > window_start_timestamp_ns",
            name=f"ck_{table}_segment_window",
        ),
        sa.CheckConstraint(
            f"manifest_checksum ~ '{_SHA256}' AND producer_fingerprint ~ '{_SHA256}'",
            name=f"ck_{table}_checksums",
        ),
        sa.CheckConstraint(counts, name=f"ck_{table}_counts"),
    ]


def _registered_at() -> sa.Column:
    return sa.Column(
        "registered_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )


def upgrade() -> None:
    _refuse_if_rows("scene_run_records", "episode_run_records", "scenes", "episodes")

    op.drop_table("scenes")
    op.drop_table("episodes")

    _unit_table("scenes", "scene_id")
    op.create_table(
        "scene_revisions",
        *_revision_columns("scenes", "scene_id"),
        sa.Column(
            "observed_channels", JSONB, nullable=False, server_default=_EMPTY_LIST
        ),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("keyframe_count", sa.Integer(), nullable=False),
        _registered_at(),
        *_revision_constraints(
            "scene_revisions",
            "scene_id",
            "observation_count >= 0 AND keyframe_count >= 0",
        ),
    )

    _unit_table("episodes", "episode_id")
    op.create_table(
        "episode_revisions",
        *_revision_columns("episodes", "episode_id"),
        *(
            sa.Column(name, JSONB, nullable=False, server_default=_EMPTY_LIST)
            for name in (
                "observation_topics",
                "state_topics",
                "action_topics",
                "event_topics",
            )
        ),
        *(
            sa.Column(name, sa.Integer(), nullable=False)
            for name in (
                "observation_count",
                "state_count",
                "action_count",
                "event_count",
            )
        ),
        _registered_at(),
        *_revision_constraints(
            "episode_revisions",
            "episode_id",
            "observation_count >= 0 AND state_count >= 0 AND action_count >= 0 "
            "AND event_count >= 0",
        ),
    )

    op.create_table(
        "dataset_version_members",
        sa.Column(
            "dataset_version_id",
            sa.String(256),
            sa.ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("unit_id", sa.String(128), primary_key=True),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("scene_id", sa.String(128), nullable=True),
        sa.Column("episode_id", sa.String(128), nullable=True),
        sa.Column("manifest_artifact_id", sa.String(128), nullable=False),
        sa.Column("manifest_checksum", sa.String(71), nullable=False),
        sa.ForeignKeyConstraint(
            ["scene_id", "manifest_artifact_id", "manifest_checksum"],
            [
                "scene_revisions.scene_id",
                "scene_revisions.manifest_artifact_id",
                "scene_revisions.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_version_members_scene_revision",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "manifest_artifact_id", "manifest_checksum"],
            [
                "episode_revisions.episode_id",
                "episode_revisions.manifest_artifact_id",
                "episode_revisions.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_version_members_episode_revision",
        ),
        sa.CheckConstraint(
            "(domain = 'scene' AND scene_id = unit_id AND episode_id IS NULL) OR "
            "(domain = 'episode' AND episode_id = unit_id AND scene_id IS NULL)",
            name="ck_dataset_version_members_revision",
        ),
    )
    op.create_index(
        "ix_dataset_version_members_scene_id", "dataset_version_members", ["scene_id"]
    )
    op.create_index(
        "ix_dataset_version_members_episode_id",
        "dataset_version_members",
        ["episode_id"],
    )

    for table, unit_table, id_column in (
        ("scene_run_records", "scene_revisions", "scene_id"),
        ("episode_run_records", "episode_revisions", "episode_id"),
    ):
        _drop_fk_on(table, ["manifest_artifact_id"])
        op.create_foreign_key(
            f"fk_{table}_revision",
            table,
            unit_table,
            [id_column, "manifest_artifact_id", "manifest_checksum"],
            [id_column, "manifest_artifact_id", "manifest_checksum"],
            ondelete="RESTRICT",
        )

    op.execute(
        """
        CREATE FUNCTION sceneops_reject_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'rows of % are immutable', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION sceneops_reject_update()"
        )


def downgrade() -> None:
    _refuse_if_rows(
        "scene_run_records",
        "episode_run_records",
        "dataset_version_members",
        "scene_revisions",
        "episode_revisions",
        "scenes",
        "episodes",
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    op.execute("DROP FUNCTION sceneops_reject_update()")

    for table in ("scene_run_records", "episode_run_records"):
        op.drop_constraint(f"fk_{table}_revision", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_manifest_artifact_id",
            table,
            "artifacts",
            ["manifest_artifact_id"],
            ["artifact_id"],
            ondelete="RESTRICT",
        )

    op.drop_table("dataset_version_members")
    op.drop_table("scene_revisions")
    op.drop_table("episode_revisions")
    op.drop_table("scenes")
    op.drop_table("episodes")

    for table, id_column, projection in (
        (
            "scenes",
            "scene_id",
            [
                sa.Column(
                    "observed_channels",
                    JSONB,
                    nullable=False,
                    server_default=_EMPTY_LIST,
                ),
                sa.Column("observation_count", sa.Integer(), nullable=False),
                sa.Column("keyframe_count", sa.Integer(), nullable=False),
            ],
        ),
        (
            "episodes",
            "episode_id",
            [
                *(
                    sa.Column(name, JSONB, nullable=False, server_default=_EMPTY_LIST)
                    for name in (
                        "observation_topics",
                        "state_topics",
                        "action_topics",
                        "event_topics",
                    )
                ),
                *(
                    sa.Column(name, sa.Integer(), nullable=False)
                    for name in (
                        "observation_count",
                        "state_count",
                        "action_count",
                        "event_count",
                    )
                ),
            ],
        ),
    ):
        op.create_table(
            table,
            sa.Column(id_column, sa.String(128), primary_key=True),
            sa.Column("dataset_id", sa.String(128), nullable=False),
            sa.Column("dataset_version", sa.String(128), nullable=False),
            sa.Column(
                "robot_run_id",
                sa.String(128),
                sa.ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("unit_key", sa.String(256), nullable=False),
            sa.Column("producer_fingerprint", sa.String(71), nullable=False),
            sa.Column(
                "manifest_artifact_id",
                sa.String(128),
                sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("manifest_checksum", sa.String(71), nullable=False),
            sa.Column("window_clock", sa.String(64), nullable=False),
            sa.Column("window_start_timestamp_ns", sa.BigInteger(), nullable=False),
            sa.Column("window_end_timestamp_ns", sa.BigInteger(), nullable=False),
            *projection,
            sa.Column(
                "registered_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=_NOW,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=_NOW,
            ),
            sa.ForeignKeyConstraint(
                ["dataset_id", "dataset_version"],
                ["dataset_versions.dataset_id", "dataset_versions.version"],
                ondelete="RESTRICT",
                name=f"fk_{table}_dataset_version",
            ),
            sa.CheckConstraint(
                "window_end_timestamp_ns > window_start_timestamp_ns",
                name=f"ck_{table}_segment_window",
            ),
        )
        op.create_index(f"ix_{table}_dataset", table, ["dataset_id", "dataset_version"])
        op.create_index(
            f"ix_{table}_recording_scope",
            table,
            ["dataset_id", "dataset_version", "robot_run_id"],
        )
        op.create_index(
            f"ix_{table}_manifest_artifact_id", table, ["manifest_artifact_id"]
        )
