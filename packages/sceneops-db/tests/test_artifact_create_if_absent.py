"""``PostgresArtifactRefRepository.create_if_absent``: concurrent Jobs that write the
same deterministic ArtifactRecord (two builds of one RobotRun's payloads or one
scope's manifests) converge on one row instead of failing on the primary key.
Real PostgreSQL.

The race needs two transactions that overlap, so the test commits its own
uniquely-identified rows into the disposable database of `make test-integration`.
"""

from __future__ import annotations

import asyncio

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.session import get_async_sessionmaker


def _ref(uri: str) -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.OBSERVATION_PAYLOAD,
        uri=uri,
        media_type="image/jpeg",
        checksum="sha256:" + "a" * 64,
        size_bytes=3,
    )


async def test_an_absent_id_is_created_and_a_present_one_is_returned(
    db_session, unique_id
):
    repository = PostgresArtifactRefRepository(db_session)
    artifact_id = unique_id("payload")

    created, was_created = await repository.create_if_absent(
        artifact_id=artifact_id, ref=_ref("s3://b/one"), job_id="job-1"
    )
    again, again_created = await repository.create_if_absent(
        artifact_id=artifact_id, ref=_ref("s3://b/other"), job_id="job-2"
    )

    assert was_created and not again_created
    assert again == created, "the existing row is returned, never overwritten"
    assert again.uri == "s3://b/one" and again.job_id == "job-1"


async def test_overlapping_transactions_on_one_id_converge_on_one_row(unique_id):
    """Both transactions insert before either commits: the second waits for the
    first and reads its row (the race that a check-then-insert lost with a
    unique violation)."""
    sessionmaker = get_async_sessionmaker()
    artifact_id = unique_id("payload")
    first_inserted = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> tuple[object, bool]:
        async with sessionmaker() as session:
            result = await PostgresArtifactRefRepository(session).create_if_absent(
                artifact_id=artifact_id, ref=_ref("s3://b/one"), job_id="job-1"
            )
            first_inserted.set()
            await release_first.wait()
            await session.commit()
            return result

    async def second() -> tuple[object, bool]:
        await first_inserted.wait()
        async with sessionmaker() as session:
            result = await PostgresArtifactRefRepository(session).create_if_absent(
                artifact_id=artifact_id, ref=_ref("s3://b/one"), job_id="job-2"
            )
            await session.commit()
            return result

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_inserted.wait()
    await asyncio.sleep(0.5)
    assert not second_task.done(), "the second writer waits for the first's transaction"

    release_first.set()
    (record_1, created_1), (record_2, created_2) = await asyncio.wait_for(
        asyncio.gather(first_task, second_task), timeout=10
    )

    assert (created_1, created_2) == (True, False)
    assert record_2 == record_1
    assert record_2.job_id == "job-1"
