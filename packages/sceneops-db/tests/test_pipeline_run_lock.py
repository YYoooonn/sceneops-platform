"""``PostgresPipelineRunRepository.get_for_update``: the row lock that serializes the
orchestration steps of one PipelineRun. Real PostgreSQL.

The lock is only observable across two connections, so the test commits its own
uniquely-identified run into the disposable database of `make test-integration`.
"""

from __future__ import annotations

import asyncio

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_db.postgres.pipelines import PostgresPipelineRunRepository
from sceneops_db.session import get_async_sessionmaker


async def test_a_second_step_waits_for_the_first_and_then_sees_its_commit(unique_id):
    sessionmaker = get_async_sessionmaker()
    run_id = unique_id("pipe")
    async with sessionmaker() as session:
        await PostgresPipelineRunRepository(session).create(
            PipelineRunManifest(
                pipeline_run_id=run_id,
                type=PipelineType.RECORDING_SCENE_BUILDING,
                status=PipelineRunStatus.QUEUED,
            )
        )
        await session.commit()

    first_holds_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def first_step() -> None:
        async with sessionmaker() as session:
            repository = PostgresPipelineRunRepository(session)
            run = await repository.get_for_update(run_id)
            assert run is not None and run.status == PipelineRunStatus.QUEUED
            first_holds_lock.set()
            await release_first.wait()
            await repository.update(
                run.model_copy(update={"status": PipelineRunStatus.RUNNING})
            )
            await session.commit()

    async def second_step() -> PipelineRunManifest | None:
        await first_holds_lock.wait()
        async with sessionmaker() as session:
            run = await PostgresPipelineRunRepository(session).get_for_update(run_id)
            await session.rollback()
            return run

    first = asyncio.create_task(first_step())
    second = asyncio.create_task(second_step())

    await first_holds_lock.wait()
    await asyncio.sleep(0.5)
    assert not second.done(), "a concurrent step must wait for the lock holder"

    release_first.set()
    await first
    seen = await asyncio.wait_for(second, timeout=10)

    assert seen is not None
    assert (
        seen.status == PipelineRunStatus.RUNNING
    ), "the waiting step must read the state the lock holder committed"


async def test_an_unknown_run_is_none(db_session, unique_id):
    repository = PostgresPipelineRunRepository(db_session)
    assert await repository.get_for_update(unique_id("pipe")) is None
