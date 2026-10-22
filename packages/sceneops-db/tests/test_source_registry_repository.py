"""The Source Registry on real PostgreSQL (ADR-015 §5, MS-1, MS-3, MS-6a):
insert-if-absent registration decided against the stored rows, races between
overlapping transactions, and the invariants PostgreSQL enforces on its own
(immutable rows, one format per content, no content without a snapshot,
undeletable snapshots).

Races need overlapping transactions, so these tests commit their own
uniquely-identified rows into the disposable database of
`make test-integration`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.sources import (
    ContentFormatConflictError,
    RegistrationOutcome,
    SnapshotContentConflictError,
    SnapshotDescriptorConflictError,
    SourceContentRecord,
    SourceSnapshotRecord,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.sources import PostgresSourceRegistryRepository
from sceneops_db.session import get_async_sessionmaker


def _hex() -> str:
    return uuid.uuid4().hex


def _sha() -> str:
    return "sha256:" + (_hex() * 2)


async def _artifact(session, artifact_id: str, kind: ArtifactKind) -> None:
    await PostgresArtifactRefRepository(session).create_if_absent(
        artifact_id=artifact_id,
        ref=ArtifactRef(
            kind=kind,
            uri=f"s3://b/{artifact_id}.json",
            media_type="application/json",
            checksum=_sha(),
            size_bytes=10,
        ),
    )


def _content(format: str = "lerobot") -> SourceContentRecord:
    content_id = f"content-{_hex()}"
    return SourceContentRecord(
        content_id=content_id,
        format=format,
        content_checksum=_sha(),
        inventory_artifact_id=f"art-sourceinventory-{content_id}",
        file_count=7,
        total_bytes=7_684_382,
    )


def _snapshot(content: SourceContentRecord, **overrides) -> SourceSnapshotRecord:
    snapshot_id = overrides.pop("snapshot_id", f"snapshot-{_hex()}")
    values = dict(
        snapshot_id=snapshot_id,
        content_id=content.content_id,
        format=content.format,
        upstream="lerobot/pusht",
        upstream_revision="rev-1",
        selection_checksum="sha256:" + "1" * 64,
        descriptor_artifact_id=f"art-sourcedescriptor-{snapshot_id}",
        descriptor_checksum="sha256:" + "2" * 64,
    )
    values.update(overrides)
    return SourceSnapshotRecord(**values)


async def _register(content, snapshot, *, commit: bool = True):
    async with get_async_sessionmaker()() as session:
        await _artifact(
            session,
            content.inventory_artifact_id,
            ArtifactKind.SOURCE_CONTENT_INVENTORY,
        )
        await _artifact(
            session,
            snapshot.descriptor_artifact_id,
            ArtifactKind.SOURCE_SNAPSHOT_DESCRIPTOR,
        )
        registration = await PostgresSourceRegistryRepository(session).register(
            content=content, snapshot=snapshot
        )
        if commit:
            await session.commit()
        return registration


async def _rows(content_id: str) -> tuple[int, int]:
    async with get_async_sessionmaker()() as session:
        contents = (
            await session.execute(
                text("SELECT count(*) FROM source_contents WHERE content_id = :c"),
                {"c": content_id},
            )
        ).scalar_one()
        snapshots = (
            await session.execute(
                text("SELECT count(*) FROM source_snapshots WHERE content_id = :c"),
                {"c": content_id},
            )
        ).scalar_one()
    return contents, snapshots


# ── registration decisions ───────────────────────────────────────────────────


async def test_register_inserts_then_converges_without_writing():
    content = _content()
    snapshot = _snapshot(content)

    first = await _register(content, snapshot)
    again = await _register(content, snapshot)

    assert (first.content_outcome, first.snapshot_outcome) == (
        RegistrationOutcome.CREATED,
        RegistrationOutcome.CREATED,
    )
    assert (again.content_outcome, again.snapshot_outcome) == (
        RegistrationOutcome.EXISTING,
        RegistrationOutcome.EXISTING,
    )
    assert again.content == first.content and again.snapshot == first.snapshot
    assert first.content.registered_at is not None
    assert await _rows(content.content_id) == (1, 1)


async def test_two_declarations_of_one_content_keep_one_content_row():
    content = _content()
    await _register(content, _snapshot(content))
    second = await _register(content, _snapshot(content, upstream_revision="rev-2"))

    assert second.content_outcome == RegistrationOutcome.EXISTING
    assert second.snapshot_outcome == RegistrationOutcome.CREATED
    async with get_async_sessionmaker()() as session:
        listed = await PostgresSourceRegistryRepository(session).list_snapshots(
            content_id=content.content_id
        )
    assert sorted(s.upstream_revision for s in listed) == ["rev-1", "rev-2"]
    assert await _rows(content.content_id) == (1, 2)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("format", ContentFormatConflictError),
        ("content", SnapshotContentConflictError),
        ("descriptor", SnapshotDescriptorConflictError),
    ],
)
async def test_conflicting_registrations_fail_and_write_nothing(change, error):
    content = _content()
    snapshot = _snapshot(content)
    await _register(content, snapshot)

    if change == "format":
        other = content.model_copy(update={"format": "nuscenes"})
        candidate = (other, _snapshot(other))
    elif change == "content":
        other = _content()
        candidate = (
            other,
            snapshot.model_copy(update={"content_id": other.content_id}),
        )
    else:
        candidate = (
            content,
            snapshot.model_copy(update={"descriptor_checksum": "sha256:" + "3" * 64}),
        )
    with pytest.raises(error):
        await _register(*candidate)

    assert await _rows(content.content_id) == (1, 1)
    if change == "content":
        assert await _rows(candidate[0].content_id) == (0, 0)


# ── invariants PostgreSQL enforces itself ───────────────────────────────────


async def test_rows_are_immutable_and_snapshots_undeletable():
    content = _content()
    snapshot = _snapshot(content)
    await _register(content, snapshot)

    for statement in (
        "UPDATE source_contents SET total_bytes = 0 WHERE content_id = :c",
        "UPDATE source_snapshots SET upstream = 'x' WHERE content_id = :c",
        "DELETE FROM source_snapshots WHERE content_id = :c",
        "DELETE FROM source_contents WHERE content_id = :c",
    ):
        async with get_async_sessionmaker()() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text(statement), {"c": content.content_id})
    assert await _rows(content.content_id) == (1, 1)


async def test_a_content_cannot_commit_without_a_snapshot():
    content = _content()
    async with get_async_sessionmaker()() as session:
        await _artifact(
            session,
            content.inventory_artifact_id,
            ArtifactKind.SOURCE_CONTENT_INVENTORY,
        )
        await session.execute(
            text(
                "INSERT INTO source_contents (content_id, format, content_checksum, "
                "inventory_artifact_id, file_count, total_bytes) "
                "VALUES (:c, 'lerobot', :s, :a, 1, 1)"
            ),
            {
                "c": content.content_id,
                "s": content.content_checksum,
                "a": content.inventory_artifact_id,
            },
        )
        with pytest.raises(IntegrityError, match="has no SourceSnapshot"):
            await session.commit()
    assert await _rows(content.content_id) == (0, 0)


async def test_a_snapshot_cannot_declare_another_format_than_its_content():
    """Bypassing the repository: the (content_id, format) foreign key alone
    refuses a second format for registered bytes."""
    content = _content()
    await _register(content, _snapshot(content))
    snapshot = _snapshot(content, format="nuscenes")
    async with get_async_sessionmaker()() as session:
        await _artifact(
            session,
            snapshot.descriptor_artifact_id,
            ArtifactKind.SOURCE_SNAPSHOT_DESCRIPTOR,
        )
        with pytest.raises(IntegrityError, match="fk_source_snapshots_content"):
            await session.execute(
                text(
                    "INSERT INTO source_snapshots (snapshot_id, content_id, format, "
                    "upstream, upstream_revision, selection_checksum, "
                    "descriptor_artifact_id, descriptor_checksum) VALUES "
                    "(:s, :c, 'nuscenes', 'u', 'r', :x, :a, :x)"
                ),
                {
                    "s": snapshot.snapshot_id,
                    "c": content.content_id,
                    "x": "sha256:" + "4" * 64,
                    "a": snapshot.descriptor_artifact_id,
                },
            )
    assert await _rows(content.content_id) == (1, 1)


# ── overlapping transactions ─────────────────────────────────────────────────


async def _overlapping(*candidates):
    """Every candidate registers in its own transaction; all have inserted
    (and so hold their keys) before any commits."""
    inserted = [asyncio.Event() for _ in candidates]
    release = asyncio.Event()

    async def _one(index, content, snapshot):
        async with get_async_sessionmaker()() as session:

            async def _write():
                await _artifact(
                    session,
                    content.inventory_artifact_id,
                    ArtifactKind.SOURCE_CONTENT_INVENTORY,
                )
                await _artifact(
                    session,
                    snapshot.descriptor_artifact_id,
                    ArtifactKind.SOURCE_SNAPSHOT_DESCRIPTOR,
                )
                return await PostgresSourceRegistryRepository(session).register(
                    content=content, snapshot=snapshot
                )

            if index == 0:
                result = await _write()
                inserted[0].set()
                await release.wait()
                await session.commit()
                return result
            await inserted[0].wait()
            # Blocks on the first transaction's uncommitted keys.
            task = asyncio.ensure_future(_write())
            await asyncio.sleep(0.3)
            waited = not task.done()
            release.set()
            result = await task
            assert waited, "the second insert must wait for the first"
            await session.commit()
            return result

    return await asyncio.gather(
        *(_one(i, c, s) for i, (c, s) in enumerate(candidates)), return_exceptions=True
    )


async def test_overlapping_registrations_of_one_snapshot_converge():
    content = _content()
    snapshot = _snapshot(content)

    first, second = await _overlapping((content, snapshot), (content, snapshot))

    assert not isinstance(first, BaseException), first
    assert not isinstance(second, BaseException), second
    assert first.snapshot_outcome == RegistrationOutcome.CREATED
    assert second.snapshot_outcome == RegistrationOutcome.EXISTING
    assert second.content_outcome == RegistrationOutcome.EXISTING
    assert await _rows(content.content_id) == (1, 1)


async def test_overlapping_snapshots_of_one_content_leave_one_content_row():
    content = _content()

    first, second = await _overlapping(
        (content, _snapshot(content)),
        (content, _snapshot(content, upstream_revision="rev-2")),
    )

    assert not isinstance(first, BaseException), first
    assert not isinstance(second, BaseException), second
    assert second.content_outcome == RegistrationOutcome.EXISTING
    assert second.snapshot_outcome == RegistrationOutcome.CREATED
    assert await _rows(content.content_id) == (1, 2)


async def test_overlapping_formats_for_one_content_leave_the_first():
    content = _content()
    other = content.model_copy(update={"format": "nuscenes"})

    first, second = await _overlapping(
        (content, _snapshot(content)), (other, _snapshot(other))
    )

    assert not isinstance(first, BaseException), first
    assert isinstance(second, ContentFormatConflictError), second
    assert await _rows(content.content_id) == (1, 1)
