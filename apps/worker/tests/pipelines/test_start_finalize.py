"""Unit tests for PipelineRunner.start / .finalize.

These bridge pipeline-level status transitions across independent
`run-pipeline-task` CLI invocations (the per-task Airflow DAG), by
recomposing PipelineRunner's existing private status-transition methods —
so these tests assert the recomposition, not the underlying transitions
(those are already covered by exercising `run()` elsewhere).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineType,
)
from sceneops_worker.pipelines.runner import PipelineRunner

PIPELINE_RUN_ID = "pipe-001"


def _pipeline_run(status: PipelineRunStatus) -> PipelineRunManifest:
    now = utc_now()
    return PipelineRunManifest(
        pipeline_run_id=PIPELINE_RUN_ID,
        type=PipelineType.DATASET_SCENE_INGESTION,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _task_run(task_id: str, status: PipelineTaskRunStatus) -> PipelineTaskRunManifest:
    now = utc_now()
    return PipelineTaskRunManifest(
        pipeline_task_run_id=f"task-{task_id}",
        pipeline_run_id=PIPELINE_RUN_ID,
        pipeline_task_id=task_id,
        pipeline_task_name=task_id,
        task_order=0,
        status=status,
        job_type=JobType.VALIDATE_SCENE,
        created_at=now,
        updated_at=now,
    )


def _context(
    pipeline_run: PipelineRunManifest, task_runs: list[PipelineTaskRunManifest]
):
    ctx = MagicMock()
    ctx.pipeline_store = MagicMock()
    ctx.pipeline_store.get = AsyncMock(return_value=pipeline_run)
    ctx.pipeline_store.list_tasks = AsyncMock(return_value=task_runs)
    ctx.pipeline_store.save = AsyncMock(side_effect=lambda run: run)
    ctx.commit = AsyncMock()
    ctx.rollback = AsyncMock()
    return ctx


# ── start ─────────────────────────────────────────────────────────────────


async def test_start_transitions_pending_to_running():
    pipeline_run = _pipeline_run(PipelineRunStatus.PENDING)
    ctx = _context(pipeline_run, [])

    result = await PipelineRunner(ctx).start(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.RUNNING
    ctx.commit.assert_awaited()


async def test_start_raises_if_already_succeeded():
    pipeline_run = _pipeline_run(PipelineRunStatus.SUCCEEDED)
    ctx = _context(pipeline_run, [])

    with pytest.raises(RuntimeError):
        await PipelineRunner(ctx).start(PIPELINE_RUN_ID)


async def test_start_allows_blocked_retry():
    pipeline_run = _pipeline_run(PipelineRunStatus.BLOCKED)
    ctx = _context(pipeline_run, [])

    result = await PipelineRunner(ctx).start(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.RUNNING


# ── finalize ──────────────────────────────────────────────────────────────


async def test_finalize_succeeds_when_all_tasks_succeeded():
    pipeline_run = _pipeline_run(PipelineRunStatus.RUNNING)
    task_runs = [
        _task_run("ingest_scenes", PipelineTaskRunStatus.SUCCEEDED),
        _task_run("register_scene", PipelineTaskRunStatus.SUCCEEDED),
    ]
    ctx = _context(pipeline_run, task_runs)

    result = await PipelineRunner(ctx).finalize(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.SUCCEEDED


async def test_finalize_blocks_when_any_task_blocked():
    pipeline_run = _pipeline_run(PipelineRunStatus.RUNNING)
    task_runs = [
        _task_run("ingest_scenes", PipelineTaskRunStatus.SUCCEEDED),
        _task_run("validate_scene", PipelineTaskRunStatus.BLOCKED),
    ]
    ctx = _context(pipeline_run, task_runs)

    result = await PipelineRunner(ctx).finalize(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.BLOCKED


async def test_finalize_fails_when_any_task_failed():
    pipeline_run = _pipeline_run(PipelineRunStatus.RUNNING)
    task_runs = [
        _task_run("ingest_scenes", PipelineTaskRunStatus.SUCCEEDED),
        _task_run("register_scene", PipelineTaskRunStatus.FAILED),
    ]
    ctx = _context(pipeline_run, task_runs)

    result = await PipelineRunner(ctx).finalize(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.FAILED


async def test_finalize_blocked_takes_precedence_over_failed():
    pipeline_run = _pipeline_run(PipelineRunStatus.RUNNING)
    task_runs = [
        _task_run("validate_scene", PipelineTaskRunStatus.BLOCKED),
        _task_run("profile_scene", PipelineTaskRunStatus.FAILED),
    ]
    ctx = _context(pipeline_run, task_runs)

    result = await PipelineRunner(ctx).finalize(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.BLOCKED


async def test_finalize_fails_when_pipeline_never_started():
    # start() never ran (or its validation rejected the pipeline) — status
    # is still PENDING/QUEUED, not RUNNING.
    pipeline_run = _pipeline_run(PipelineRunStatus.QUEUED)
    ctx = _context(pipeline_run, [])

    result = await PipelineRunner(ctx).finalize(PIPELINE_RUN_ID)

    assert result.status == PipelineRunStatus.FAILED
