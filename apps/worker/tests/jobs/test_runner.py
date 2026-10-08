"""Unit tests for JobRunner execution-state lifecycle."""

from __future__ import annotations

import asyncio
import time
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
from sceneops_worker.jobs.runner import (
    JobNotClaimableError,
    JobOwnershipLostError,
    JobRunner,
)


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


def _make_context(job: JobManifest, *, lease_seconds: float = 60.0) -> MagicMock:
    ctx = MagicMock()
    ctx.worker_id = "worker-001"
    ctx.settings.runtime.job_lease_seconds = lease_seconds
    ctx.commit = AsyncMock()
    ctx.rollback = AsyncMock()
    ctx.job_store = MagicMock()
    ctx.job_store.get = AsyncMock(return_value=job)
    ctx.job_store.save_owned = AsyncMock(side_effect=lambda j, *, lease_generation: j)

    async def _claim_for_run(
        job_id: str,
        *,
        worker_id: str,
        runnable_statuses: set[JobStatus],
        lease_seconds: float,
    ) -> JobManifest | None:
        # Mirrors the real repository contract: a claim only succeeds when the
        # job's current status is one of the runnable statuses; otherwise the
        # runner falls back to _load_job()/_validate_runnable() for the
        # specific "already succeeded/running/cancelled" error.
        if job.status not in runnable_statuses:
            return None
        job.lease_generation += 1
        return job

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


# ── terminal state ────────────────────────────────────────────────────────────


class _Ledger:
    """What PostgreSQL would hold: a job write becomes durable only at commit,
    and a rollback discards the uncommitted ones. ``save_owned`` fences like the
    repository: it writes only while the durable Job is RUNNING."""

    def __init__(self, ctx: MagicMock) -> None:
        self.durable: list[JobStatus] = []
        self._uncommitted: list[JobStatus] = []
        self.attempted: list[JobStatus] = []
        self.lose_ownership_from: JobStatus | None = None

        async def commit() -> None:
            self.durable.extend(self._uncommitted)
            self._uncommitted.clear()

        async def rollback() -> None:
            self._uncommitted.clear()

        async def save_owned(
            job: JobManifest, *, lease_generation: int
        ) -> JobManifest | None:
            self.attempted.append(job.status)
            if self.lose_ownership_from == job.status:
                return None
            terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED}
            assert not (
                self.durable and self.durable[-1] in terminal
            ), f"{job.status} written over a terminal Job: {self.durable}"
            self._uncommitted.append(job.status)
            return job

        ctx.commit = AsyncMock(side_effect=commit)
        ctx.rollback = AsyncMock(side_effect=rollback)
        ctx.job_store.save_owned = AsyncMock(side_effect=save_owned)


def _fail_events(ctx: MagicMock, *event_types: JobEventType) -> None:
    async def append(event) -> None:
        if event.type in event_types:
            raise RuntimeError("event store unavailable")

    ctx.job_event_store.append = AsyncMock(side_effect=append)


class TestJobRunnerTerminalState:
    """A Job leaves RUNNING once, written by its owner; what is recorded after
    the terminal commit cannot change it."""

    async def test_an_event_failure_after_committed_success_keeps_the_job_succeeded(
        self,
    ) -> None:
        job = _make_job().model_copy(update={"pipeline_run_id": "pipe-001"})
        ctx = _make_context(job)
        ledger = _Ledger(ctx)
        _fail_events(ctx, JobEventType.SUCCEEDED)
        dispatcher = MagicMock()

        finished = await JobRunner(
            ctx,
            dispatcher=dispatcher,
            handler_registry=_make_registry(_SimpleResult(value="done")),
        ).run("job-001")

        assert ledger.durable == [JobStatus.RUNNING, JobStatus.SUCCEEDED]
        assert ledger.attempted == [JobStatus.RUNNING, JobStatus.SUCCEEDED]
        assert finished.status == JobStatus.SUCCEEDED
        # The pipeline still observes the committed terminal Job.
        dispatcher.advance_pipeline.assert_called_once_with("pipe-001")

    async def test_an_event_failure_after_committed_failure_keeps_the_job_failed(
        self,
    ) -> None:
        job = _make_job()
        ctx = _make_context(job)
        ledger = _Ledger(ctx)
        _fail_events(ctx, JobEventType.FAILED)

        finished = await JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_failing_registry(RuntimeError("boom")),
        ).run("job-001")

        assert ledger.durable == [JobStatus.RUNNING, JobStatus.FAILED]
        assert finished.status == JobStatus.FAILED
        assert finished.error.message == "boom"

    async def test_success_is_written_exactly_once(self) -> None:
        job = _make_job()
        ctx = _make_context(job)
        ledger = _Ledger(ctx)

        await JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
        ).run("job-001")

        assert ledger.attempted == [JobStatus.RUNNING, JobStatus.SUCCEEDED]

    async def test_a_worker_that_lost_the_job_writes_no_terminal_state(self) -> None:
        job = _make_job().model_copy(update={"pipeline_run_id": "pipe-001"})
        ctx = _make_context(job)
        ledger = _Ledger(ctx)
        ledger.lose_ownership_from = JobStatus.SUCCEEDED
        dispatcher = MagicMock()

        with pytest.raises(JobOwnershipLostError):
            await JobRunner(
                ctx,
                dispatcher=dispatcher,
                handler_registry=_make_registry(_SimpleResult(value="done")),
            ).run("job-001")

        # No FAILED fallback over the Job another party now owns, no events and
        # no pipeline report from a worker that no longer owns the Job.
        assert ledger.attempted == [JobStatus.RUNNING, JobStatus.SUCCEEDED]
        assert ledger.durable == [JobStatus.RUNNING]
        assert JobEventType.SUCCEEDED not in _emitted_event_types(ctx)
        dispatcher.advance_pipeline.assert_not_called()

    async def test_a_worker_that_lost_the_job_before_the_handler_does_not_run_it(
        self,
    ) -> None:
        job = _make_job()
        ctx = _make_context(job)
        ledger = _Ledger(ctx)
        ledger.lose_ownership_from = JobStatus.RUNNING
        registry = _make_registry(_SimpleResult(value="done"))

        with pytest.raises(JobOwnershipLostError):
            await JobRunner(ctx, dispatcher=MagicMock(), handler_registry=registry).run(
                "job-001"
            )

        registry.get.return_value.run.assert_not_awaited()
        assert ledger.durable == []


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


# ── claim identity ────────────────────────────────────────────────────────────


class TestJobRunnerClaimIdentity:
    async def test_the_locked_event_names_the_claim_and_the_process_holding_it(
        self,
    ) -> None:
        import os
        import socket

        job = _make_job()
        job.lease_generation = 2  # claimed twice before: this claim is the third
        ctx = _make_context(job)

        await JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
        ).run("job-001")

        (locked,) = [
            c.args[0]
            for c in ctx.job_event_store.append.await_args_list
            if c.args[0].type == JobEventType.LOCKED
        ]
        assert locked.attempt == 3
        assert locked.data["worker_node"] == socket.gethostname()
        assert locked.data["pid"] == os.getpid()
        assert locked.data["worker_id"] == "worker-001"


# ── validation guards ─────────────────────────────────────────────────────────


class TestJobRunnerValidation:
    @pytest.mark.parametrize(
        "status", [JobStatus.SUCCEEDED, JobStatus.RUNNING, JobStatus.CANCELLED]
    )
    async def test_a_job_that_is_not_runnable_is_refused_as_not_claimable(
        self, status: JobStatus
    ) -> None:
        ctx = _make_context(_make_job(status=status))

        with pytest.raises(JobNotClaimableError):
            await JobRunner(
                ctx,
                dispatcher=MagicMock(),
                handler_registry=MagicMock(spec=JobHandlerRegistry),
            ).run("job-001")

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


# ── claim lease ───────────────────────────────────────────────────────────────


class _Renewal:
    """A LeaseRenewal whose answers the test scripts: True (renewed), False
    (the claim is gone) or an exception (could not tell)."""

    def __init__(self, *answers: bool | Exception, then: bool | Exception = True):
        self.answers = list(answers)
        self.then = then
        self.calls: list[tuple[str, int]] = []

    async def renew(self, job_id, *, lease_generation, lease_seconds) -> bool:
        self.calls.append((job_id, lease_generation))
        answer = self.answers.pop(0) if self.answers else self.then
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def close(self) -> None:
        pass


def _make_handler_registry(run) -> JobHandlerRegistry:
    handler = MagicMock()
    handler.job_type = JobType.PROFILE_SCENE
    handler.params_model = MagicMock()
    handler.params_model.model_validate = MagicMock(return_value=MagicMock())
    handler.run = run
    registry = MagicMock(spec=JobHandlerRegistry)
    registry.get = MagicMock(return_value=handler)
    return registry


class TestJobRunnerLease:
    """The claim's lease is renewed while the Job runs; every write is fenced by
    the claim's generation; a lost claim cancels the handler."""

    async def test_every_write_is_fenced_by_the_claims_generation(self) -> None:
        job = _make_job().model_copy(update={"lease_generation": 4})
        ctx = _make_context(job)

        await JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_registry(_SimpleResult(value="ok")),
            lease_renewal=_Renewal,
        ).run("job-001")

        generations = {
            c.kwargs["lease_generation"]
            for c in ctx.job_store.save_owned.await_args_list
        }
        assert generations == {5}
        assert ctx.job_store.claim_for_run.await_args.kwargs["lease_seconds"] == 60.0

    async def test_a_lost_lease_cancels_the_handler_and_writes_nothing(self) -> None:
        job = _make_job().model_copy(update={"pipeline_run_id": "pipe-001"})
        ctx = _make_context(job, lease_seconds=0.06)
        ledger = _Ledger(ctx)
        renewal = _Renewal(True, False)
        dispatcher = MagicMock()
        cancelled = asyncio.Event()

        async def run_forever(request):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with pytest.raises(JobOwnershipLostError, match="handler was cancelled"):
            await JobRunner(
                ctx,
                dispatcher=dispatcher,
                handler_registry=_make_handler_registry(run_forever),
                lease_renewal=lambda: renewal,
            ).run("job-001")

        assert cancelled.is_set()
        assert len(renewal.calls) == 2
        # No terminal write, no FAILED fallback and no pipeline report from a
        # worker whose claim is gone.
        assert ledger.attempted == [JobStatus.RUNNING]
        assert ledger.durable == [JobStatus.RUNNING]
        dispatcher.advance_pipeline.assert_not_called()

    async def test_a_renewal_that_cannot_tell_is_not_a_lost_lease(self) -> None:
        job = _make_job()
        ctx = _make_context(job, lease_seconds=0.03)
        renewal = _Renewal(OSError("connection refused"), OSError("timeout"))

        async def run_briefly(request):
            await asyncio.sleep(0.15)
            return _SimpleResult(value="ok")

        finished = await JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_handler_registry(run_briefly),
            lease_renewal=lambda: renewal,
        ).run("job-001")

        assert finished.status == JobStatus.SUCCEEDED
        assert len(renewal.calls) > 2  # kept renewing after the two errors

    async def test_the_lease_is_renewed_while_a_handler_blocks_the_event_loop(
        self,
    ) -> None:
        job = _make_job()
        ctx = _make_context(job, lease_seconds=0.06)
        renewal = _Renewal()

        async def block_the_loop(request):
            time.sleep(0.3)  # synchronous work on the worker's event loop
            return _SimpleResult(value="ok")

        finished = await JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=_make_handler_registry(block_the_loop),
            lease_renewal=lambda: renewal,
        ).run("job-001")

        assert finished.status == JobStatus.SUCCEEDED
        # ~0.3 s / 0.02 s renewal interval while the loop could not run a task.
        assert len(renewal.calls) >= 5

    async def test_a_lease_lost_before_the_handler_skips_it(self) -> None:
        job = _make_job()
        ctx = _make_context(job, lease_seconds=0.03)
        _Ledger(ctx)
        renewal = _Renewal(then=False)
        registry = _make_registry(_SimpleResult(value="ok"))
        start_step = JobRunner._start_step

        async def slow_start_step(self, execution):
            await asyncio.sleep(0.1)  # the keeper reports the loss meanwhile
            await start_step(self, execution)

        runner = JobRunner(
            ctx,
            dispatcher=MagicMock(),
            handler_registry=registry,
            lease_renewal=lambda: renewal,
        )
        runner._start_step = slow_start_step.__get__(runner)

        with pytest.raises(JobOwnershipLostError, match="before its handler ran"):
            await runner.run("job-001")

        registry.get.return_value.run.assert_not_called()
