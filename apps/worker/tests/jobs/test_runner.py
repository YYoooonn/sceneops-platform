"""Unit tests for JobRunner execution-state lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobEventType,
    JobManifest,
    JobStatus,
    JobStepStatus,
    JobType,
)
from sceneops_core.jobs.schemas.steps import JobStep
from sceneops_worker.jobs.registry import JobHandlerRegistry
from sceneops_worker.jobs.runner import JobRunner


class _SimpleResult(BaseModel):
    value: str


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_job(
    *,
    job_id: str = "job-001",
    status: JobStatus = JobStatus.PENDING,
    steps: list[JobStep] | None = None,
    params: dict | None = None,
) -> JobManifest:
    now = utc_now()
    return JobManifest(
        job_id=job_id,
        type=JobType.BUILD_SCENE_INDEX,
        status=status,
        params=params or {},
        steps=steps or [],
        created_at=now,
        updated_at=now,
    )


def _make_step(
    *,
    step_id: str = "step-001",
    step_name: str = "extract",
    status: JobStepStatus = JobStepStatus.PENDING,
) -> JobStep:
    return JobStep(
        job_step_id=step_id,
        job_step_name=step_name,
        status=status,
    )


def _make_context(job: JobManifest) -> MagicMock:
    ctx = MagicMock()
    ctx.worker_id = "worker-001"
    ctx.commit = AsyncMock()
    ctx.rollback = AsyncMock()
    ctx.job_store = MagicMock()
    ctx.job_store.get = AsyncMock(return_value=job)
    ctx.job_store.save = AsyncMock(side_effect=lambda j: j)
    ctx.job_event_store = MagicMock()
    ctx.job_event_store.append = AsyncMock()
    return ctx


def _make_registry(result: BaseModel) -> JobHandlerRegistry:
    handler = MagicMock()
    handler.job_type = JobType.BUILD_SCENE_INDEX
    handler.params_model = MagicMock()
    handler.params_model.model_validate = MagicMock(return_value=MagicMock())
    handler.run = AsyncMock(return_value=result)
    registry = MagicMock(spec=JobHandlerRegistry)
    registry.get = MagicMock(return_value=handler)
    return registry


def _make_failing_registry(exc: Exception) -> JobHandlerRegistry:
    handler = MagicMock()
    handler.job_type = JobType.BUILD_SCENE_INDEX
    handler.params_model = MagicMock()
    handler.params_model.model_validate = MagicMock(return_value=MagicMock())
    handler.run = AsyncMock(side_effect=exc)
    registry = MagicMock(spec=JobHandlerRegistry)
    registry.get = MagicMock(return_value=handler)
    return registry


def _emitted_event_types(ctx: MagicMock) -> list[JobEventType]:
    return [c.args[0].type for c in ctx.job_event_store.append.await_args_list]


# ── success path ──────────────────────────────────────────────────────────────


class TestJobRunnerSuccessPath:
    async def test_returns_succeeded_job(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_registry(_SimpleResult(value="done"))
        )

        finished = await runner.run("job-001")

        assert finished.status == JobStatus.SUCCEEDED
        assert finished.result == {"value": "done"}

    async def test_emits_started_and_succeeded_events(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_registry(_SimpleResult(value="ok"))
        )

        await runner.run("job-001")

        types = _emitted_event_types(ctx)
        assert JobEventType.STARTED in types
        assert JobEventType.SUCCEEDED in types

    async def test_no_step_events_without_steps(self) -> None:
        job = _make_job(steps=[])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_registry(_SimpleResult(value="ok"))
        )

        await runner.run("job-001")

        types = _emitted_event_types(ctx)
        assert JobEventType.STEP_STARTED not in types
        assert JobEventType.STEP_SUCCEEDED not in types


# ── step events ───────────────────────────────────────────────────────────────


class TestJobRunnerStepEvents:
    async def test_step_events_emitted_when_step_exists(self) -> None:
        job = _make_job(steps=[_make_step()])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_registry(_SimpleResult(value="ok"))
        )

        await runner.run("job-001")

        types = _emitted_event_types(ctx)
        assert JobEventType.STEP_STARTED in types
        assert JobEventType.STEP_SUCCEEDED in types

    async def test_step_event_order(self) -> None:
        job = _make_job(steps=[_make_step()])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_registry(_SimpleResult(value="ok"))
        )

        await runner.run("job-001")

        types = _emitted_event_types(ctx)
        assert types.index(JobEventType.STARTED) < types.index(
            JobEventType.STEP_STARTED
        )
        assert types.index(JobEventType.STEP_STARTED) < types.index(
            JobEventType.STEP_SUCCEEDED
        )
        assert types.index(JobEventType.STEP_SUCCEEDED) < types.index(
            JobEventType.SUCCEEDED
        )


# ── failure path ──────────────────────────────────────────────────────────────


class TestJobRunnerFailurePath:
    async def test_reraises_original_exception(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_failing_registry(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError, match="boom"):
            await runner.run("job-001")

    async def test_rollback_called_on_failure(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_failing_registry(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            await runner.run("job-001")

        ctx.rollback.assert_awaited_once()

    async def test_emits_failed_event_on_failure(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_failing_registry(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            await runner.run("job-001")

        assert JobEventType.FAILED in _emitted_event_types(ctx)

    async def test_step_failed_event_emitted_when_step_exists(self) -> None:
        job = _make_job(steps=[_make_step()])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_failing_registry(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            await runner.run("job-001")

        assert JobEventType.STEP_FAILED in _emitted_event_types(ctx)

    async def test_no_step_failed_event_without_steps(self) -> None:
        job = _make_job(steps=[])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_failing_registry(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            await runner.run("job-001")

        assert JobEventType.STEP_FAILED not in _emitted_event_types(ctx)

    async def test_failed_job_uses_latest_execution_job(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx, handler_registry=_make_failing_registry(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            await runner.run("job-001")

        # The FAILED event should carry the job_id from the latest execution state.
        failed_events = [
            c.args[0]
            for c in ctx.job_event_store.append.await_args_list
            if c.args[0].type == JobEventType.FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0].job_id == "job-001"


# ── validation guards ─────────────────────────────────────────────────────────


class TestJobRunnerValidation:
    async def test_already_succeeded_raises(self) -> None:
        job = _make_job(status=JobStatus.SUCCEEDED)
        ctx = _make_context(job)
        runner = JobRunner(ctx, handler_registry=MagicMock(spec=JobHandlerRegistry))

        with pytest.raises(RuntimeError, match="already succeeded"):
            await runner.run("job-001")

    async def test_already_running_raises(self) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        ctx = _make_context(job)
        runner = JobRunner(ctx, handler_registry=MagicMock(spec=JobHandlerRegistry))

        with pytest.raises(RuntimeError, match="already running"):
            await runner.run("job-001")

    async def test_cancelled_raises(self) -> None:
        job = _make_job(status=JobStatus.CANCELLED)
        ctx = _make_context(job)
        runner = JobRunner(ctx, handler_registry=MagicMock(spec=JobHandlerRegistry))

        with pytest.raises(RuntimeError, match="cancelled"):
            await runner.run("job-001")

    async def test_job_not_found_raises(self) -> None:
        ctx = _make_context(_make_job())
        ctx.job_store.get = AsyncMock(return_value=None)
        runner = JobRunner(ctx, handler_registry=MagicMock(spec=JobHandlerRegistry))

        with pytest.raises(FileNotFoundError, match="job-001"):
            await runner.run("job-001")
