"""Unit tests for REGISTER_SOURCE_SNAPSHOT (sceneops_worker.sources.registration).

No Postgres: a small transactional fake registry (writes become visible only
on commit, discarded on rollback) applies the production decision rules, so
every failure path can assert "no registry change". Published objects are
written by the real ``publish_snapshot`` into a real LocalArtifactStore that
registration may only read.

The same rules against real PostgreSQL + MinIO (unique-key races, the
deferred content trigger, unusual keys) are covered in
test_source_registration_integration.py.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.config import ArtifactSettings
from sceneops_core.sources import (
    ContentFormatConflictError,
    RegistrationOutcome,
    SnapshotContentConflictError,
    SnapshotDeclaration,
    SnapshotDescriptorConflictError,
    SnapshotSelection,
    SourceSnapshotDescriptor,
    decide_content_registration,
    decide_snapshot_registration,
)
from sceneops_db.repositories import SourceRegistration
from sceneops_sources import descriptor_uri, inventory_uri, publish_snapshot
from sceneops_storage import LocalArtifactStore
from sceneops_worker.sources.registration import (
    ContentVerificationError,
    PublishedSnapshotInvalidError,
    SnapshotNotPublishedError,
    UntrustedSourceLocationError,
    register_source_snapshot,
)

TREE = {
    "README.md": b"# tiny\n",
    "data/chunk-000/file-000.parquet": b"PAR1" + bytes(range(256)) * 64 + b"PAR1",
    "meta/info.json": b'{"fps": 10}',
    "meta/episodes/chunk-000/file-000.parquet": b"PAR1episodesPAR1",
}
INCLUDE = ("README.md", "data/**", "meta/**")


def _declaration(
    *, revision: str = "rev-1", format: str = "lerobot", include=INCLUDE
) -> SnapshotDeclaration:
    return SnapshotDeclaration(
        format=format,
        upstream="lerobot/tiny",
        upstream_revision=revision,
        selection=SnapshotSelection(include=tuple(include)),
    )


class _Registry:
    """Committed rows + the current transaction's staged rows."""

    def __init__(self) -> None:
        self.committed: dict[str, dict] = {"contents": {}, "snapshots": {}, "art": {}}
        self.staged: dict[str, dict] = {k: {} for k in self.committed}

    def view(self, table: str) -> dict:
        return {**self.committed[table], **self.staged[table]}

    def commit(self) -> None:
        for table, rows in self.staged.items():
            self.committed[table].update(rows)
        self.rollback()

    def rollback(self) -> None:
        self.staged = {k: {} for k in self.committed}

    def state(self) -> tuple:
        return tuple(dict(rows) for rows in self.committed.values())


class _SourceStore:
    def __init__(self, registry: _Registry) -> None:
        self._registry = registry
        self.register_error: BaseException | None = None

    async def get_content(self, content_id):
        return self._registry.view("contents").get(content_id)

    async def get_snapshot(self, snapshot_id):
        return self._registry.view("snapshots").get(snapshot_id)

    async def register(self, *, content, snapshot):
        if self.register_error is not None:
            raise self.register_error
        stored = await self.get_content(content.content_id)
        content_outcome = decide_content_registration(content, stored)
        if stored is None:
            self._registry.staged["contents"][content.content_id] = content
        stored = await self.get_snapshot(snapshot.snapshot_id)
        snapshot_outcome = decide_snapshot_registration(snapshot, stored)
        if stored is None:
            self._registry.staged["snapshots"][snapshot.snapshot_id] = snapshot
        return SourceRegistration(
            content=await self.get_content(content.content_id),
            snapshot=await self.get_snapshot(snapshot.snapshot_id),
            content_outcome=content_outcome,
            snapshot_outcome=snapshot_outcome,
        )


class _ArtifactRecordStore:
    def __init__(self, registry: _Registry) -> None:
        self._registry = registry

    async def register(self, *, artifact_id, ref, owner_type, owner_id, job_id):
        existing = self._registry.view("art").get(artifact_id)
        if existing is not None:
            assert (existing.uri, existing.checksum) == (ref.uri, ref.checksum)
            return existing, False
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=ref.kind.value,
            uri=ref.uri,
            media_type=ref.media_type,
            size_bytes=ref.size_bytes,
            checksum=ref.checksum,
            owner_type=owner_type.value,
            owner_id=owner_id,
            job_id=job_id,
        )
        self._registry.staged["art"][artifact_id] = record
        return record, True


class _ReadOnlyStore(LocalArtifactStore):
    """Registration never writes an object; it records what it read."""

    def __init__(self, *, root_uri: str) -> None:
        super().__init__(root_uri=root_uri)
        self.reads: list[str] = []

    async def read_bytes(self, uri):
        self.reads.append(uri)
        return await super().read_bytes(uri)

    async def write_bytes(self, uri, data):  # pragma: no cover - must not run
        raise AssertionError(f"registration wrote {uri}")


class _Context:
    def __init__(self, artifact_root: Path) -> None:
        self.registry = _Registry()
        self.settings = SimpleNamespace(
            artifact=ArtifactSettings(root_uri=str(artifact_root))
        )
        self.artifact_store = _ReadOnlyStore(root_uri=str(artifact_root))
        self.source_store = _SourceStore(self.registry)
        self.artifact_record_store = _ArtifactRecordStore(self.registry)

    async def commit(self) -> None:
        self.registry.commit()

    async def rollback(self) -> None:
        self.registry.rollback()

    @property
    def sources_root(self) -> str:
        return self.settings.artifact.source_root_uri


_EMPTY = ({}, {}, {})


@pytest.fixture()
def artifact_root(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture()
def context(artifact_root: Path) -> _Context:
    return _Context(artifact_root)


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for relative, data in TREE.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return root


async def _publish(context: _Context, source_dir: Path, **declaration):
    return await publish_snapshot(
        store=LocalArtifactStore(root_uri=context.sources_root),
        sources_root=context.sources_root,
        source_dir=source_dir,
        declaration=_declaration(**declaration),
        annotations={"license": "mit"},
    )


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ── registration and convergence ─────────────────────────────────────────────


async def test_registers_content_snapshot_and_their_artifact_records(
    context, source_dir
) -> None:
    published = await _publish(context, source_dir)

    registration = await register_source_snapshot(
        context=context, descriptor_uri=published.descriptor_uri, job_id="job-1"
    )

    assert registration.content_outcome == RegistrationOutcome.CREATED
    assert registration.snapshot_outcome == RegistrationOutcome.CREATED
    contents, snapshots, artifacts = context.registry.state()
    content = contents[published.content_id]
    assert (content.format, content.file_count, content.total_bytes) == (
        "lerobot",
        4,
        sum(len(d) for d in TREE.values()),
    )
    snapshot = snapshots[published.snapshot_id]
    assert snapshot.upstream_revision == "rev-1"
    assert snapshot.descriptor_checksum == _sha(
        Path(published.descriptor_uri).read_bytes()
    )
    by_kind = {a.kind: a for a in artifacts.values()}
    assert set(by_kind) == {
        ArtifactKind.SOURCE_CONTENT_INVENTORY.value,
        ArtifactKind.SOURCE_SNAPSHOT_DESCRIPTOR.value,
    }
    inventory_record = by_kind[ArtifactKind.SOURCE_CONTENT_INVENTORY.value]
    assert inventory_record.uri == published.inventory_uri
    assert inventory_record.checksum == _sha(Path(published.inventory_uri).read_bytes())
    assert (inventory_record.owner_type, inventory_record.owner_id) == (
        "source_content",
        published.content_id,
    )
    assert content.inventory_artifact_id == inventory_record.artifact_id
    descriptor_record = by_kind[ArtifactKind.SOURCE_SNAPSHOT_DESCRIPTOR.value]
    assert (descriptor_record.owner_type, descriptor_record.owner_id) == (
        "source_snapshot",
        published.snapshot_id,
    )
    assert {a.job_id for a in artifacts.values()} == {"job-1"}
    # The first registration of a content reads every preserved file.
    assert sum("/files/" in uri for uri in context.artifact_store.reads) == len(TREE)


async def test_reregistration_is_a_no_op_that_reads_only_the_descriptor(
    context, source_dir
) -> None:
    published = await _publish(context, source_dir)
    await register_source_snapshot(
        context=context, descriptor_uri=published.descriptor_uri
    )
    before = context.registry.state()
    context.artifact_store.reads.clear()

    again = await register_source_snapshot(
        context=context, descriptor_uri=published.descriptor_uri, job_id="job-2"
    )

    assert (
        again.content_outcome == again.snapshot_outcome == RegistrationOutcome.EXISTING
    )
    assert context.registry.state() == before
    assert context.artifact_store.reads == [published.descriptor_uri]


async def test_second_declaration_of_identical_bytes_reuses_the_content(
    context, source_dir
) -> None:
    first = await _publish(context, source_dir)
    await register_source_snapshot(context=context, descriptor_uri=first.descriptor_uri)
    second = await _publish(context, source_dir, revision="rev-2")
    assert second.files_written == 0 and not second.inventory_written
    context.artifact_store.reads.clear()

    registration = await register_source_snapshot(
        context=context, descriptor_uri=second.descriptor_uri
    )

    assert registration.content_outcome == RegistrationOutcome.EXISTING
    assert registration.snapshot_outcome == RegistrationOutcome.CREATED
    contents, snapshots, artifacts = context.registry.state()
    assert list(contents) == [first.content_id]
    assert {s.content_id for s in snapshots.values()} == {first.content_id}
    assert len(snapshots) == 2 and len(artifacts) == 3
    # Verified at its first registration and write-once: no file is re-read.
    assert not any("/files/" in uri for uri in context.artifact_store.reads)


# ── trusted storage ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spelling",
    [
        "{other}/sources/snapshots/{sid}/descriptor.json",
        "{root}/../elsewhere/sources/snapshots/{sid}/descriptor.json",
        "{root}/sources//snapshots/{sid}/descriptor.json",
        "{root}/sources/snapshots/{sid}/other.json",
        "{root}/sources/contents/{sid}/descriptor.json",
        "{root}/sources/snapshots/not-a-snapshot/descriptor.json",
        "s3://bucket/sources/snapshots/{sid}/descriptor.json",
    ],
)
async def test_only_descriptors_under_the_configured_source_root_are_read(
    context, source_dir, artifact_root, tmp_path, spelling
) -> None:
    published = await _publish(context, source_dir)
    uri = spelling.format(
        root=artifact_root, other=tmp_path / "other", sid=published.snapshot_id
    )

    with pytest.raises(UntrustedSourceLocationError):
        await register_source_snapshot(context=context, descriptor_uri=uri)

    assert context.artifact_store.reads == []
    assert context.registry.state() == _EMPTY


# ── unfinished or invalid publication ────────────────────────────────────────


async def test_publication_interrupted_before_the_descriptor_is_not_registrable(
    context, source_dir, monkeypatch
) -> None:
    """A publisher that dies before its marker leaves files and an inventory
    but no descriptor: nothing is registrable, and the re-run converges by
    reusing every object, after which registration succeeds."""
    store = LocalArtifactStore(root_uri=context.sources_root)
    real_write = LocalArtifactStore.write_bytes

    async def _crash_on_descriptor(self, uri, data):
        if uri.endswith("descriptor.json"):
            raise RuntimeError("publisher killed")
        await real_write(self, uri, data)

    monkeypatch.setattr(LocalArtifactStore, "write_bytes", _crash_on_descriptor)
    with pytest.raises(RuntimeError, match="killed"):
        await publish_snapshot(
            store=store,
            sources_root=context.sources_root,
            source_dir=source_dir,
            declaration=_declaration(),
            annotations={"license": "mit"},
        )
    monkeypatch.undo()
    target = descriptor_uri(store, context.sources_root, _declaration().snapshot_id)

    with pytest.raises(SnapshotNotPublishedError):
        await register_source_snapshot(context=context, descriptor_uri=target)
    assert context.registry.state() == _EMPTY

    rerun = await _publish(context, source_dir)
    assert (rerun.files_written, rerun.files_reused) == (0, len(TREE))
    assert not rerun.inventory_written and rerun.descriptor_written
    registration = await register_source_snapshot(
        context=context, descriptor_uri=target
    )
    assert registration.snapshot_outcome == RegistrationOutcome.CREATED


async def test_descriptor_stored_under_another_snapshot_key_is_refused(
    context, source_dir
) -> None:
    published = await _publish(context, source_dir)
    store = LocalArtifactStore(root_uri=context.sources_root)
    forged = descriptor_uri(store, context.sources_root, "snapshot-" + "0" * 32)
    await store.write_bytes(forged, Path(published.descriptor_uri).read_bytes())

    with pytest.raises(PublishedSnapshotInvalidError, match="holds the descriptor"):
        await register_source_snapshot(context=context, descriptor_uri=forged)
    assert context.registry.state() == _EMPTY


async def test_non_canonical_descriptor_bytes_are_refused(context, source_dir) -> None:
    published = await _publish(context, source_dir)
    path = Path(published.descriptor_uri)
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(PublishedSnapshotInvalidError, match="canonical"):
        await register_source_snapshot(
            context=context, descriptor_uri=published.descriptor_uri
        )
    assert context.registry.state() == _EMPTY


async def test_missing_inventory_is_an_unfinished_publication(
    context, source_dir
) -> None:
    published = await _publish(context, source_dir)
    Path(published.inventory_uri).unlink()

    with pytest.raises(SnapshotNotPublishedError, match="inventory"):
        await register_source_snapshot(
            context=context, descriptor_uri=published.descriptor_uri
        )
    assert context.registry.state() == _EMPTY


@pytest.mark.parametrize(
    ("include", "message"),
    [
        (("README.md", "meta/**"), "does not select"),
        (("README.md", "data/**", "meta/**", "videos/**"), "match no file"),
    ],
)
async def test_selection_must_select_exactly_the_inventory(
    context, source_dir, include, message
) -> None:
    """A descriptor whose declared selection disagrees with the inventory it
    names (forged directly, since publication derives one from the other)."""
    published = await _publish(context, source_dir)
    store = LocalArtifactStore(root_uri=context.sources_root)
    forged = SourceSnapshotDescriptor(
        declaration=_declaration(include=include),
        content_id=published.content_id,
        annotations={"license": "mit"},
    )
    uri = descriptor_uri(store, context.sources_root, forged.snapshot_id)
    await store.write_bytes(uri, forged.to_canonical_bytes())

    with pytest.raises(PublishedSnapshotInvalidError, match=message):
        await register_source_snapshot(context=context, descriptor_uri=uri)
    assert context.registry.state() == _EMPTY


# ── stored content verification ──────────────────────────────────────────────


async def _content_file(published, relative: str) -> Path:
    return Path(published.inventory_uri).parent / "files" / relative


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("flip_byte", "sha256"),
        ("truncate", "another size"),
        ("delete", "not stored"),
        ("extra_object", "not inventoried"),
    ],
)
async def test_altered_stored_content_fails_registration(
    context, source_dir, tamper, message
) -> None:
    published = await _publish(context, source_dir)
    target = await _content_file(published, "data/chunk-000/file-000.parquet")
    data = target.read_bytes()
    if tamper == "flip_byte":
        target.write_bytes(data[:-1] + bytes([data[-1] ^ 0xFF]))
    elif tamper == "truncate":
        target.write_bytes(data[:-1])
    elif tamper == "delete":
        target.unlink()
    else:
        (target.parent / "stray.bin").write_bytes(b"x")

    with pytest.raises(ContentVerificationError, match=message):
        await register_source_snapshot(
            context=context, descriptor_uri=published.descriptor_uri
        )
    assert context.registry.state() == _EMPTY


# ── declaration conflicts (ADR-015 §3) ───────────────────────────────────────


async def test_same_bytes_under_another_format_conflict(context, source_dir) -> None:
    first = await _publish(context, source_dir)
    await register_source_snapshot(context=context, descriptor_uri=first.descriptor_uri)
    before = context.registry.state()
    other = await _publish(context, source_dir, format="other-format")
    assert other.content_id == first.content_id

    with pytest.raises(ContentFormatConflictError):
        await register_source_snapshot(
            context=context, descriptor_uri=other.descriptor_uri
        )
    assert context.registry.state() == before


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("bytes", SnapshotContentConflictError),
        ("annotations", SnapshotDescriptorConflictError),
    ],
)
async def test_registered_declaration_with_another_descriptor_conflicts(
    context, source_dir, change, error
) -> None:
    """The descriptor key is write-once, so a second descriptor for a
    registered declaration reaches registration only if the stored one was
    replaced out of band; registration refuses it on its own."""
    first = await _publish(context, source_dir)
    await register_source_snapshot(context=context, descriptor_uri=first.descriptor_uri)
    before = context.registry.state()
    Path(first.descriptor_uri).unlink()
    if change == "bytes":
        (source_dir / "meta" / "info.json").write_bytes(b'{"fps": 30}')
        second = await _publish(context, source_dir)
    else:
        second = await publish_snapshot(
            store=LocalArtifactStore(root_uri=context.sources_root),
            sources_root=context.sources_root,
            source_dir=source_dir,
            declaration=_declaration(),
            annotations={"license": "apache-2.0"},
        )
    assert second.snapshot_id == first.snapshot_id

    with pytest.raises(error):
        await register_source_snapshot(
            context=context, descriptor_uri=second.descriptor_uri
        )
    assert context.registry.state() == before


async def test_failure_inside_the_transaction_leaves_no_rows(
    context, source_dir
) -> None:
    published = await _publish(context, source_dir)
    context.source_store.register_error = RuntimeError("injected after ArtifactRecords")

    with pytest.raises(RuntimeError, match="injected"):
        await register_source_snapshot(
            context=context, descriptor_uri=published.descriptor_uri
        )
    assert context.registry.state() == _EMPTY


async def test_registration_reads_the_inventory_from_the_trusted_root_only(
    context, source_dir, tmp_path
) -> None:
    """The inventory location is derived from the configured root and the
    descriptor's content id; no caller-supplied location is followed."""
    published = await _publish(context, source_dir)
    store = LocalArtifactStore(root_uri=context.sources_root)
    assert published.inventory_uri == inventory_uri(
        store, context.sources_root, published.content_id
    )
    shutil.copytree(context.sources_root, tmp_path / "copy")

    await register_source_snapshot(
        context=context, descriptor_uri=published.descriptor_uri
    )

    assert all(
        uri.startswith(context.sources_root + "/")
        for uri in context.artifact_store.reads
    )
