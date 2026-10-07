"""Publication of one immutable artifact revision against real PostgreSQL and
real MinIO: concurrent Jobs converge on one object and one ArtifactRecord, the
record's checksum is the checksum of the bytes in the bucket, changed content
is a new revision beside the old one, and an object at a pinned key is never
replaced.

Requires the disposable environment of ``make test-integration``; skips
otherwise. Every test uses unique ids.
"""

from __future__ import annotations

import asyncio

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import sha256_checksum
from sceneops_db.models.artifacts import ArtifactModel
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.derived import DerivedManifestConflictError
from sceneops_worker.derived.publication import publish_registered
from sqlalchemy import select


@pytest.fixture()
def settings(_fresh_database_connection, _minio_reachable, worker_settings):
    # create_worker_context caches its ArtifactStore per process.
    dependencies_module._artifact_store = None
    dependencies_module._input_store = None
    yield worker_settings
    dependencies_module._artifact_store = None
    dependencies_module._input_store = None


async def _publish(settings, run_id: str, document: dict, *, job_id: str):
    """One Job: its own session and transaction, committed after publishing."""
    async with get_async_sessionmaker()() as session:
        context = create_worker_context(session, settings=settings, worker_id=job_id)
        artifact = await publish_registered(
            context,
            kind=ArtifactKind.DATASET_VALIDATION_REPORT,
            prefix="valreport",
            logical_id=run_id,
            directory=context.artifact_store.join_uri(
                settings.run_root_uri, "scene_validations", run_id
            ),
            stem="report",
            data=canonical_json_bytes(document),
            media_type="application/json",
            owner_type=ArtifactOwnerType.SCENE_VALIDATION_RUN,
            owner_id=run_id,
            run_id=run_id,
            job_id=job_id,
        )
        await session.commit()
        return artifact


async def _record(artifact_id: str) -> ArtifactModel | None:
    async with get_async_sessionmaker()() as session:
        rows = await session.execute(
            select(ArtifactModel).where(ArtifactModel.artifact_id == artifact_id)
        )
        return rows.scalar_one_or_none()


async def _store(settings):
    async with get_async_sessionmaker()() as session:
        return create_worker_context(
            session, settings=settings, worker_id="reader"
        ).artifact_store


async def test_concurrent_jobs_publishing_one_revision_converge(settings, unique_id):
    run_id = unique_id("val")
    document = {"run_id": run_id, "status": "ready", "issues": []}

    artifacts = await asyncio.gather(
        *(_publish(settings, run_id, document, job_id=f"job-{i}") for i in range(6))
    )

    assert len({a.artifact_id for a in artifacts}) == 1
    assert len({a.uri for a in artifacts}) == 1
    record = await _record(artifacts[0].artifact_id)
    assert record is not None
    # The record pins exactly the bytes in the bucket.
    data = await (await _store(settings)).read_bytes(record.uri)
    assert data == canonical_json_bytes(document)
    assert record.checksum == sha256_checksum(data)
    assert record.size_bytes == len(data)
    # The first registration's Job is the one on the record.
    assert record.job_id in {f"job-{i}" for i in range(6)}


async def test_changed_content_is_a_new_revision_beside_the_old_one(
    settings, unique_id
):
    run_id = unique_id("val")
    first = await _publish(
        settings, run_id, {"run_id": run_id, "status": "ready"}, job_id="job-1"
    )
    second = await _publish(
        settings, run_id, {"run_id": run_id, "status": "failed"}, job_id="job-1"
    )

    assert first.artifact_id != second.artifact_id
    assert first.uri != second.uri
    store = await _store(settings)
    for artifact in (first, second):
        record = await _record(artifact.artifact_id)
        assert record is not None
        assert record.checksum == sha256_checksum(await store.read_bytes(record.uri))


async def test_an_object_at_a_pinned_key_is_never_replaced(settings, unique_id):
    run_id = unique_id("val")
    document = {"run_id": run_id, "status": "ready"}
    first = await _publish(settings, run_id, document, job_id="job-1")

    store = await _store(settings)
    await store.write_bytes(first.uri, b"tampered")

    with pytest.raises(DerivedManifestConflictError):
        await _publish(settings, run_id, document, job_id="job-2")
    assert await store.read_bytes(first.uri) == b"tampered"
