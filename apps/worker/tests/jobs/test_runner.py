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
        type=JobType.PROFILE_SCENE,
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

    async def _claim_for_run(
        job_id: str, *, worker_id: str, runnable_statuses: set[JobStatus]
    ) -> JobManifest | None:
        # Mirrors the real repository contract: a claim only succeeds when the
        # job's current status is one of the runnable statuses; otherwise the
        # runner falls back to _load_job()/_validate_runnable() for the
        # specific "already succeeded/running/cancelled" error.
        return job if job.status in runnable_statuses else None

    ctx.job_store.claim_for_run = AsyncMock(side_effect=_claim_for_run)
    ctx.job_event_store = MagicMock()
    ctx.job_event_store.append = AsyncMock()
    return ctx


def _make_registry(result: BaseModel) -> JobHandlerRegistry:
    handler = MagicMock()
    handler.job_type = JobType.PROFILE_SCENE
    handler.params_model = MagicMock()
    handler.params_model.model_validate = MagicMock(return_value=MagicMock())
    handler.run = AsyncMock(return_value=result)
    registry = MagicMock(spec=JobHandlerRegistry)
    registry.get = MagicMock(return_value=handler)
    return registry


def _make_failing_registry(exc: Exception) -> JobHandlerRegistry:
    handler = MagicMock()
    handler.job_type = JobType.PROFILE_SCENE
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
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="done")),
        )

        finished = await runner.run("job-001")

        assert finished.status == JobStatus.SUCCEEDED
        assert finished.result == {"value": "done"}

    async def test_emits_started_and_succeeded_events(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
        )

        await runner.run("job-001")

        types = _emitted_event_types(ctx)
        assert JobEventType.STARTED in types
        assert JobEventType.SUCCEEDED in types

    async def test_no_step_events_without_steps(self) -> None:
        job = _make_job(steps=[])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
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
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
        )

        await runner.run("job-001")

        types = _emitted_event_types(ctx)
        assert JobEventType.STEP_STARTED in types
        assert JobEventType.STEP_SUCCEEDED in types

    async def test_step_event_order(self) -> None:
        job = _make_job(steps=[_make_step()])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
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
    """A handler failure is the Job's persisted outcome, not an exception."""

    async def test_returns_the_failed_job_with_the_handler_error(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        )

        finished = await runner.run("job-001")

        assert finished.status == JobStatus.FAILED
        assert finished.error.type == "RuntimeError"
        assert finished.error.message == "boom"

    async def test_rollback_called_on_failure(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        )

        await runner.run("job-001")

        ctx.rollback.assert_awaited_once()

    async def test_emits_failed_event_on_failure(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        )

        await runner.run("job-001")

        assert JobEventType.FAILED in _emitted_event_types(ctx)

    async def test_step_failed_event_emitted_when_step_exists(self) -> None:
        job = _make_job(steps=[_make_step()])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        )

        await runner.run("job-001")

        assert JobEventType.STEP_FAILED in _emitted_event_types(ctx)

    async def test_no_step_failed_event_without_steps(self) -> None:
        job = _make_job(steps=[])
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        )

        await runner.run("job-001")

        assert JobEventType.STEP_FAILED not in _emitted_event_types(ctx)

    async def test_failed_job_uses_latest_execution_job(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        )

        await runner.run("job-001")

        # The FAILED event should carry the job_id from the latest execution state.
        failed_events = [
            c.args[0]
            for c in ctx.job_event_store.append.await_args_list
            if c.args[0].type == JobEventType.FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0].job_id == "job-001"


# ── pipeline report ───────────────────────────────────────────────────────────


class TestJobRunnerPipelineReport:
    """A pipeline-owned Job reports its terminal state so the orchestrator can
    advance the run; a standalone Job reports nothing."""

    @pytest.mark.parametrize("fails", [False, True])
    async def test_a_pipeline_job_advances_its_pipeline_once_terminal(
        self, fails: bool
    ) -> None:
        job = _make_job().model_copy(update={"pipeline_run_id": "pipe-001"})
        ctx = _make_context(job)
        dispatcher = MagicMock()
        registry = (
            _make_failing_registry(RuntimeError("boom"))
            if fails
            else _make_registry(_SimpleResult(value="ok"))
        )

        def _advance(pipeline_run_id: str) -> None:
            # Reported only after the terminal state is committed.
            assert ctx.commit.await_count > 0
            assert job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)

        dispatcher.advance_pipeline.side_effect = _advance
        await JobRunner(ctx, dispatcher=dispatcher, handler_registry=registry).run(
            "job-001"
        )

        dispatcher.advance_pipeline.assert_called_once_with("pipe-001")
        dispatcher.dispatch_job.assert_not_called()

    async def test_a_standalone_job_reports_nothing(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        dispatcher = MagicMock()

        await JobRunner(
            ctx,
            dispatcher=dispatcher,
            handler_registry=_make_registry(_SimpleResult(value="ok")),
        ).run("job-001")

        dispatcher.advance_pipeline.assert_not_called()

    async def test_a_job_that_cannot_be_claimed_reports_nothing(self) -> None:
        job = _make_job(status=JobStatus.RUNNING).model_copy(
            update={"pipeline_run_id": "pipe-001"}
        )
        ctx = _make_context(job)
        dispatcher = MagicMock()

        with pytest.raises(RuntimeError, match="already running"):
            await JobRunner(
                ctx,
                dispatcher=dispatcher,
                handler_registry=MagicMock(spec=JobHandlerRegistry),
            ).run("job-001")

        dispatcher.advance_pipeline.assert_not_called()


# ── validation guards ─────────────────────────────────────────────────────────


class TestJobRunnerValidation:
    async def test_already_succeeded_raises(self) -> None:
        job = _make_job(status=JobStatus.SUCCEEDED)
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=MagicMock(spec=JobHandlerRegistry),
        )

        with pytest.raises(RuntimeError, match="already succeeded"):
            await runner.run("job-001")

    async def test_already_running_raises(self) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=MagicMock(spec=JobHandlerRegistry),
        )

        with pytest.raises(RuntimeError, match="already running"):
            await runner.run("job-001")

    async def test_cancelled_raises(self) -> None:
        job = _make_job(status=JobStatus.CANCELLED)
        ctx = _make_context(job)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=MagicMock(spec=JobHandlerRegistry),
        )

        with pytest.raises(RuntimeError, match="cancelled"):
            await runner.run("job-001")

    async def test_job_not_found_raises(self) -> None:
        ctx = _make_context(_make_job())
        # No job exists at all: claim fails (no matching row) and the
        # fallback lookup also finds nothing.
        ctx.job_store.claim_for_run = AsyncMock(return_value=None)
        ctx.job_store.get = AsyncMock(return_value=None)
        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=MagicMock(spec=JobHandlerRegistry),
        )

        with pytest.raises(FileNotFoundError, match="job-001"):
            await runner.run("job-001")
