"""DatasetVersion membership invariants

Revision ID: e9b5d3a7c2f4
Revises: d7a3c9e1f5b2
Create Date: 2026-10-18

ADR-015 §7, MS-8, enforced by PostgreSQL itself rather than only by the
repository's single write:

    complete     a committed version has exactly the members its summary
                 counts, per domain: a deferred constraint trigger checks
                 every inserted version row when its transaction commits,
                 so a positive count with no member rows cannot commit
    closed       a member can only be inserted in the transaction that
                 inserted its version row; a member of a version committed
                 earlier is refused
    undeletable  a member row is never deleted (UPDATE is already refused
                 by sceneops_reject_update); with members RESTRICTing their
                 version, a committed version cannot be deleted either

"Inserted by this transaction" is decided from the version row's xmin. The
composer inserts the version row in a savepoint, so xmin can be a
subtransaction id; pg_xact_status reports the current transaction and its
subtransactions as "in progress", and a version row inserted by another
in-progress transaction is invisible, which the member foreign key refuses.
xmin is a 32-bit xid: it is widened with the epoch closest to the current
transaction id (a subtransaction id is larger than its parent's).

No row changes. The downgrade drops the triggers and functions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e9b5d3a7c2f4"
down_revision: str | None = "d7a3c9e1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION sceneops_check_dataset_version_members() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            scenes bigint;
            episodes bigint;
        BEGIN
            SELECT count(*) FILTER (WHERE domain = 'scene'),
                   count(*) FILTER (WHERE domain = 'episode')
              INTO scenes, episodes
              FROM dataset_version_members
             WHERE dataset_version_id = NEW.id;
            IF scenes <> NEW.scene_count OR episodes <> NEW.episode_count THEN
                RAISE EXCEPTION
                    'DatasetVersion % has % scene and % episode member(s); '
                    'its summary counts % and %',
                    NEW.id, scenes, episodes, NEW.scene_count, NEW.episode_count
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER dataset_versions_members_complete "
        "AFTER INSERT ON dataset_versions DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION sceneops_check_dataset_version_members()"
    )

    op.execute(
        """
        CREATE FUNCTION sceneops_reject_member_of_committed_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            version_xmin xid;
            current_xid bigint;
            inserted_by bigint;
        BEGIN
            SELECT xmin INTO version_xmin
              FROM dataset_versions WHERE id = NEW.dataset_version_id;
            IF NOT FOUND THEN
                -- The member foreign key reports the missing version.
                RETURN NEW;
            END IF;
            current_xid := pg_current_xact_id()::text::bigint;
            inserted_by := (current_xid & ~4294967295::bigint)
                | version_xmin::text::bigint;
            IF inserted_by - current_xid > 2147483648 THEN
                inserted_by := inserted_by - 4294967296;
            ELSIF current_xid - inserted_by > 2147483648 THEN
                inserted_by := inserted_by + 4294967296;
            END IF;
            IF pg_xact_status(inserted_by::text::xid8)
                    IS DISTINCT FROM 'in progress' THEN
                RAISE EXCEPTION
                    'DatasetVersion % is committed; its membership is immutable',
                    NEW.dataset_version_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dataset_version_members_closed "
        "BEFORE INSERT ON dataset_version_members FOR EACH ROW "
        "EXECUTE FUNCTION sceneops_reject_member_of_committed_version()"
    )

    op.execute(
        """
        CREATE FUNCTION sceneops_reject_delete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'rows of % are never deleted', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dataset_version_members_undeletable "
        "BEFORE DELETE ON dataset_version_members FOR EACH ROW "
        "EXECUTE FUNCTION sceneops_reject_delete()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER dataset_version_members_undeletable ON dataset_version_members"
    )
    op.execute("DROP FUNCTION sceneops_reject_delete()")
    op.execute("DROP TRIGGER dataset_version_members_closed ON dataset_version_members")
    op.execute("DROP FUNCTION sceneops_reject_member_of_committed_version()")
    op.execute("DROP TRIGGER dataset_versions_members_complete ON dataset_versions")
    op.execute("DROP FUNCTION sceneops_check_dataset_version_members()")
