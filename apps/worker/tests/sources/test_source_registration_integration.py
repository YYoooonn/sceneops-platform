"""publish-snapshot + REGISTER_SOURCE_SNAPSHOT against real MinIO and real
PostgreSQL: object keys that are unusual in a URI, publication interrupted on
S3, bytes altered in the store, overlapping registrations in separate
sessions, and the persisted Job path.

Requires the disposable database and bucket of `make test-integration`;
skips (and so fails there) otherwise.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from sceneops_core.sources import (
    ContentFormatConflictError,
    RegistrationOutcome,
    SnapshotDeclaration,
    SnapshotSelection,
)
from sceneops_db.session import get_async_sessionmaker
from sceneops_sources import StoredContentFiles, publish_snapshot
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.sources.registration import (
    ContentVerificationError,
    SnapshotNotPublishedError,
    UntrustedSourceLocationError,
    register_source_snapshot,
)

# Names a preserved file may carry: nuScenes timestamps with '+', and
# characters a URI parser would treat as a query, fragment or escape.
TREE = {
    "samples/CAM_FRONT/n015-2018-07-24-11-22-45+0800__CAM_FRONT__1532402927612460.jpg": (
        b"\xff\xd8jpeg" + bytes(range(256)) * 32
    ),
    "sweeps/LIDAR_TOP/n015-2018-07-24-11-22-45+0800__LIDAR_TOP__1532402927647951.pcd.bin": (
        b"\x00" * 4096
    ),
    "odd/with space.txt": b"space",
    "odd/query?part.txt": b"query",
    "odd/fragment#part.txt": b"fragment",
    "odd/percent%2B.txt": b"percent",
    "odd/ünïcødé-名前.txt": "unicode".encode(),
    "LICENSE": b"terms\n",
}
INCLUDE = ("LICENSE", "odd/*", "samples/**", "sweeps/**")


def _declaration(revision: str, format: str = "nuscenes"):
    return SnapshotDeclaration(
        format=format,
        upstream="nuscenes",
        upstream_revision=revision,
        selection=SnapshotSelection(include=INCLUDE),
    )


@pytest.fixture()
def run_id(unique_id) -> str:
    """Content and snapshot ids are derived from bytes and declarations, not
    from storage roots, while the disposable database is shared by every
    test: each test preserves its own bytes under its own declaration."""
    return unique_id("run")


@pytest.fixture()
def source_dir(tmp_path: Path, run_id: str) -> Path:
    for relative, data in TREE.items():
        path = tmp_path / "source" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data + (run_id.encode() if relative == "LICENSE" else b""))
    return tmp_path / "source"


@pytest.fixture()
def publish(worker_settings, source_dir, run_id):
    async def _publish(store=None, *, revision: str = "", **declaration):
        return await publish_snapshot(
            store=store or S3ArtifactStore(settings=worker_settings.artifact),
            sources_root=worker_settings.artifact.source_root_uri,
            source_dir=source_dir,
            declaration=_declaration(f"{run_id}{revision}", **declaration),
            annotations={"license": "nuscenes-terms-of-use"},
        )

    return _publish


async def _registry_rows(content_id: str) -> dict[str, int]:
    async with get_async_sessionmaker()() as session:
        counts = {}
        for table, column in (
            ("source_contents", "content_id"),
            ("source_snapshots", "content_id"),
            ("artifacts", "owner_id"),
        ):
            counts[table] = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {column} = :c"),
                    {"c": content_id},
                )
            ).scalar_one()
    return counts


async def test_unusual_keys_are_preserved_registered_and_read_back(
    worker_context, worker_settings, publish, source_dir, run_id, tmp_path
) -> None:
    published = await publish()
    store = S3ArtifactStore(settings=worker_settings.artifact)
    listed = await store.list_objects(
        published.inventory_uri.removesuffix("inventory.json") + "files"
    )
    assert len(listed) == len(TREE)
    assert all(
        obj.uri.endswith(path) for obj, path in zip(listed, sorted(TREE), strict=True)
    )

    registration = await register_source_snapshot(
        context=worker_context, descriptor_uri=published.descriptor_uri, job_id="job-1"
    )
    assert registration.content_outcome == RegistrationOutcome.CREATED
    assert (registration.content.file_count, registration.content.total_bytes) == (
        len(TREE),
        sum(len(d) for d in TREE.values()) + len(run_id),
    )

    files = await StoredContentFiles.materialize(
        store=store,
        inventory_uri=published.inventory_uri,
        content_checksum=registration.content.content_checksum,
        workdir=tmp_path / "work",
    )
    for relative in TREE:
        assert (
            files.local_path(relative).read_bytes()
            == (source_dir / relative).read_bytes()
        )
    assert await _registry_rows(published.content_id) == {
        "source_contents": 1,
        "source_snapshots": 1,
        "artifacts": 1,  # the inventory; the descriptor is owned by the snapshot
    }


async def test_publication_interrupted_on_s3_is_recovered_by_rerunning_it(
    worker_context, worker_settings, publish, run_id
) -> None:
    store = S3ArtifactStore(settings=worker_settings.artifact)
    real_write = store.write_bytes
    writes = 0

    async def _dying_write(uri, data):
        nonlocal writes
        if writes == 3:
            raise ConnectionError("publisher lost the store")
        writes += 1
        await real_write(uri, data)

    store.write_bytes = _dying_write
    with pytest.raises(ConnectionError):
        await publish(store)
    target = (
        f"{worker_settings.artifact.source_root_uri}/snapshots/"
        f"{_declaration(run_id).snapshot_id}/descriptor.json"
    )
    with pytest.raises(SnapshotNotPublishedError):
        await register_source_snapshot(context=worker_context, descriptor_uri=target)

    rerun = await publish()
    assert (rerun.files_reused, rerun.files_written) == (3, len(TREE) - 3)
    assert rerun.descriptor_uri == target
    registration = await register_source_snapshot(
        context=worker_context, descriptor_uri=target
    )
    assert registration.snapshot_outcome == RegistrationOutcome.CREATED


async def test_an_object_altered_in_minio_fails_registration_without_rows(
    worker_context, worker_settings, publish
) -> None:
    published = await publish()
    store = S3ArtifactStore(settings=worker_settings.artifact)
    victim = published.inventory_uri.removesuffix("inventory.json") + (
        "files/odd/fragment#part.txt"
    )
    await store.write_bytes(victim, b"fragmenT")  # same size, other bytes

    with pytest.raises(ContentVerificationError, match="sha256"):
        await register_source_snapshot(
            context=worker_context, descriptor_uri=published.descriptor_uri
        )
    assert await _registry_rows(published.content_id) == {
        "source_contents": 0,
        "source_snapshots": 0,
        "artifacts": 0,
    }


async def test_a_descriptor_outside_the_configured_root_is_never_read(
    worker_context, worker_settings, publish
) -> None:
    published = await publish()
    elsewhere = published.descriptor_uri.replace("/sources/", "/elsewhere/sources/")
    store = S3ArtifactStore(settings=worker_settings.artifact)
    await store.write_bytes(elsewhere, await store.read_bytes(published.descriptor_uri))

    with pytest.raises(UntrustedSourceLocationError):
        await register_source_snapshot(context=worker_context, descriptor_uri=elsewhere)


async def _concurrently(worker_settings, *descriptor_uris):
    barrier = asyncio.Barrier(len(descriptor_uris))

    async def _attempt(uri):
        async with get_async_sessionmaker()() as session:
            context = create_worker_context(
                session, settings=worker_settings, worker_id="concurrent-test"
            )
            await barrier.wait()
            return await register_source_snapshot(context=context, descriptor_uri=uri)

    dependencies_module._artifact_store = None
    try:
        return await asyncio.gather(
            *(_attempt(uri) for uri in descriptor_uris), return_exceptions=True
        )
    finally:
        dependencies_module._artifact_store = None


@pytest.mark.usefixtures("_fresh_database_connection", "_minio_reachable")
async def test_concurrent_registrations_of_one_snapshot_converge(
    worker_settings, publish
) -> None:
    published = await publish()

    results = await _concurrently(
        worker_settings, published.descriptor_uri, published.descriptor_uri
    )

    assert not any(isinstance(r, BaseException) for r in results), results
    assert sorted(r.snapshot_outcome for r in results) == [
        RegistrationOutcome.CREATED,
        RegistrationOutcome.EXISTING,
    ]
    assert results[0].snapshot == results[1].snapshot
    assert (await _registry_rows(published.content_id))["source_snapshots"] == 1


@pytest.mark.usefixtures("_fresh_database_connection", "_minio_reachable")
async def test_concurrent_snapshots_of_one_content_leave_one_content_row(
    worker_settings, publish
) -> None:
    first = await publish()
    second = await publish(revision="-again")
    assert second.files_written == 0

    results = await _concurrently(
        worker_settings, first.descriptor_uri, second.descriptor_uri
    )

    assert not any(isinstance(r, BaseException) for r in results), results
    assert {r.snapshot.snapshot_id for r in results} == {
        first.snapshot_id,
        second.snapshot_id,
    }
    assert await _registry_rows(first.content_id) == {
        "source_contents": 1,
        "source_snapshots": 2,
        "artifacts": 1,
    }


@pytest.mark.usefixtures("_fresh_database_connection", "_minio_reachable")
async def test_concurrent_formats_for_one_content_have_one_winner(
    worker_settings, publish
) -> None:
    first = await publish()
    other = await publish(format="other-format")

    results = await _concurrently(
        worker_settings, first.descriptor_uri, other.descriptor_uri
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1, results
    assert isinstance(failures[0], ContentFormatConflictError)
    assert await _registry_rows(first.content_id) == {
        "source_contents": 1,
        "source_snapshots": 1,
        "artifacts": 1,
    }


async def test_register_job_records_its_snapshot_and_converges(
    worker_context, publish, unique_id
) -> None:
    """The persisted Job path (params as JobService stores them, handler
    registry, typed result) run twice for one descriptor."""
    from sceneops_core.common.time import utc_now
    from sceneops_core.jobs.schemas import (
        JobManifest,
        JobStatus,
        JobType,
        create_initial_job_steps,
        parse_job_params,
    )
    from sceneops_worker.jobs.runner import JobRunner

    published = await publish()

    async def _run_job():
        params = parse_job_params(
            JobType.REGISTER_SOURCE_SNAPSHOT,
            {"descriptor_uri": published.descriptor_uri},
        ).model_dump()
        now = utc_now()
        job = await worker_context.job_store.create(
            JobManifest(
                job_id=unique_id("job"),
                type=JobType.REGISTER_SOURCE_SNAPSHOT,
                status=JobStatus.PENDING,
                params=params,
                steps=create_initial_job_steps(JobType.REGISTER_SOURCE_SNAPSHOT),
                execution_key=unique_id("key"),
                queued_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await worker_context.commit()
        return job, await JobRunner(worker_context, dispatcher=MagicMock()).run(
            job.job_id
        )

    first_job, first = await _run_job()
    _, second = await _run_job()

    assert first.status == second.status == JobStatus.SUCCEEDED
    assert first.result["snapshot_id"] == published.snapshot_id
    assert first.result["content_id"] == published.content_id
    assert (first.result["content"], first.result["snapshot"]) == ("created", "created")
    assert (second.result["content"], second.result["snapshot"]) == (
        "existing",
        "existing",
    )
    assert first.result["file_count"] == len(TREE)
    async with get_async_sessionmaker()() as session:
        job_ids = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT job_id FROM artifacts WHERE owner_id IN (:c, :s)"
                    ),
                    {"c": published.content_id, "s": published.snapshot_id},
                )
            )
            .scalars()
            .all()
        )
    assert job_ids == [first_job.job_id]
