"""Source Registry: source_contents and source_snapshots

Revision ID: b6e2d8f4a1c7
Revises: a3f7c1e9d5b8
Create Date: 2026-10-22

ADR-015 §5, MS-1, MS-3, MS-6a: REGISTER_SOURCE_SNAPSHOT inserts one
SourceContent (the preserved bytes, pinned by its inventory ArtifactRecord)
and one SourceSnapshot (an upstream declaration, pinned by its descriptor
ArtifactRecord) in one transaction. PostgreSQL enforces:

    immutable     no row of either table is updated (sceneops_reject_update)
    declared      a content commits only with at least one snapshot (deferred
                  constraint trigger); snapshots are never deleted
                  (sceneops_reject_delete), and they RESTRICT their content,
                  so a registered content keeps its declarations
    one format    a snapshot references (content_id, format), so it can only
                  declare the format its content was first registered as
    pinned bytes  inventory / descriptor ArtifactRecords RESTRICT, one per row

No existing row changes. The downgrade refuses while either table has rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6e2d8f4a1c7"
down_revision: str | None = "a3f7c1e9d5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA256 = "'^sha256:[0-9a-f]{64}$'"
_TABLES = ("source_snapshots", "source_contents")


def upgrade() -> None:
    op.create_table(
        "source_contents",
        sa.Column("content_id", sa.String(64), primary_key=True),
        sa.Column("format", sa.String(64), nullable=False),
        sa.Column("content_checksum", sa.String(71), nullable=False),
        sa.Column(
            "inventory_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("file_count", sa.Integer, nullable=False),
        sa.Column("total_bytes", sa.BigInteger, nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "content_id ~ '^content-[0-9a-f]{32}$'", name="ck_source_contents_id"
        ),
        sa.CheckConstraint(
            f"content_checksum ~ {_SHA256}", name="ck_source_contents_checksum"
        ),
        sa.CheckConstraint(
            "file_count > 0 AND total_bytes >= 0", name="ck_source_contents_counts"
        ),
        sa.UniqueConstraint("content_checksum"),
        sa.UniqueConstraint("inventory_artifact_id"),
        sa.UniqueConstraint("content_id", "format", name="uq_source_contents_format"),
    )
    op.create_table(
        "source_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("content_id", sa.String(64), nullable=False),
        sa.Column("format", sa.String(64), nullable=False),
        sa.Column("upstream", sa.String(256), nullable=False),
        sa.Column("upstream_revision", sa.String(256), nullable=False),
        sa.Column("selection_checksum", sa.String(71), nullable=False),
        sa.Column(
            "descriptor_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("descriptor_checksum", sa.String(71), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "snapshot_id ~ '^snapshot-[0-9a-f]{32}$'", name="ck_source_snapshots_id"
        ),
        sa.CheckConstraint(
            f"selection_checksum ~ {_SHA256} AND descriptor_checksum ~ {_SHA256}",
            name="ck_source_snapshots_checksums",
        ),
        sa.ForeignKeyConstraint(
            ["content_id", "format"],
            ["source_contents.content_id", "source_contents.format"],
            name="fk_source_snapshots_content",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("descriptor_artifact_id"),
    )
    op.create_index(
        "ix_source_snapshots_content_id", "source_snapshots", ["content_id"]
    )

    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION sceneops_reject_update()"
        )
    op.execute(
        "CREATE TRIGGER source_snapshots_undeletable BEFORE DELETE ON "
        "source_snapshots FOR EACH ROW EXECUTE FUNCTION sceneops_reject_delete()"
    )
    op.execute(
        """
        CREATE FUNCTION sceneops_check_source_content_declared() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM source_snapshots WHERE content_id = NEW.content_id
            ) THEN
                RAISE EXCEPTION
                    'SourceContent % has no SourceSnapshot; a content is '
                    'registered together with its first snapshot',
                    NEW.content_id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER source_contents_declared "
        "AFTER INSERT ON source_contents DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION sceneops_check_source_content_declared()"
    )


def downgrade() -> None:
    for table in _TABLES:
        count = (
            op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        )
        if count:
            raise RuntimeError(
                f"{table} holds {count} registered row(s); reset development state"
            )
    op.execute("DROP TRIGGER source_contents_declared ON source_contents")
    op.execute("DROP FUNCTION sceneops_check_source_content_declared()")
    op.drop_table("source_snapshots")
    op.drop_table("source_contents")
