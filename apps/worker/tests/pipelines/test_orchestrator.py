"""PipelineOrchestrator over in-memory durable state.

The orchestrator never executes a Job: every step either settles the Job a task is
waiting on or submits the next task's Job to the dispatcher and returns. These tests
play the job workers by settling submitted Jobs themselves, then advance again, the
way a Job report does.
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_core.pipelines.builtin import get_pipeline_definition
from sceneops_core.pipelines.schemas import (
    PipelineInputRef,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskInputs,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineType,
)
from sceneops_worker.pipelines.orchestrator import PipelineOrchestrator

RUN_ID = "pipe-1"
SCENE_TASKS = [
    "build_recording_scenes",
    "register_scenes",
    "validate_scene",
    "profile_scene",
]


class _State:
    """The PostgreSQL rows one orchestration step reads and writes."""

    def __init__(self, run: PipelineRunManifest, tasks: list[PipelineTaskRunManifest]):
        self.run = run
        self.tasks = {t.pipeline_task_id: t for t in tasks}
        self.jobs: dict[str, JobManifest] = {}
        self.executions: list[ExecutionDispatchResult] = []
        self.commits = 0
        self.locked: list[str] = []


def _copy(model):
    return model.model_copy(deep=True)


def _context(state: _State) -> MagicMock:
    ctx = MagicMock()
    ctx.worker_id = "celery:advance"

    async def get_for_update(run_id: str):
        state.locked.append(run_id)
        return _copy(state.run) if run_id == state.run.pipeline_run_id else None

    async def list_tasks(run_id: str):
        return sorted(
            (_copy(t) for t in state.tasks.values()), key=lambda t: t.task_order
        )

    async def save(run):
        state.run = _copy(run)
        return _copy(run)

    async def save_task(task):
        state.tasks[task.pipeline_task_id] = _copy(task)
        return _copy(task)

    async def create_job(job):
        state.jobs[job.job_id] = _copy(job)
        return _copy(job)

    async def get_job(job_id):
        job = state.jobs.get(job_id)
        return _copy(job) if job else None

    async def create_execution(execution):
        state.executions.append(execution)
        return execution

    async def commit():
        state.commits += 1

    ctx.pipeline_store.get_for_update = AsyncMock(side_effect=get_for_update)
    ctx.pipeline_store.list_tasks = AsyncMock(side_effect=list_tasks)
    ctx.pipeline_store.save = AsyncMock(side_effect=save)
    ctx.pipeline_store.save_task = AsyncMock(side_effect=save_task)
    ctx.job_store.create = AsyncMock(side_effect=create_job)
    ctx.job_store.get = AsyncMock(side_effect=get_job)
    ctx.job_event_store.append = AsyncMock()
    ctx.execution_store.create = AsyncMock(side_effect=create_execution)
    ctx.commit = AsyncMock(side_effect=commit)
    ctx.rollback = AsyncMock()
    ctx.session.begin_nested = MagicMock(side_effect=lambda: _savepoint())
    return ctx


@contextlib.asynccontextmanager
async def _savepoint():
    yield


def _dispatcher() -> MagicMock:
    dispatcher = MagicMock()
    dispatcher.dispatch_job.side_effect = lambda job_id: ExecutionDispatchResult(
        execution_id=f"exec-{job_id}",
        execution_backend=ExecutionBackend.CELERY,
        execution_kind=ExecutionKind.JOB_RUN,
        resource_id=job_id,
    )
    return dispatcher


class _Planner:
    def __init__(self) -> None:
        self.count = 0
        self.fail_for: set[str] = set()

    def build_job_for_task(self, *, pipeline_run, task, inputs) -> JobManifest:
        if task.pipeline_task_id in self.fail_for:
            raise ValueError(f"no params for {task.pipeline_task_id}")
        self.count += 1
        now = utc_now()
        return JobManifest(
            job_id=f"job-{self.count}-{task.pipeline_task_id}",
            type=task.job_type,
            status=JobStatus.QUEUED,
            params=dict(inputs.refs),
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_task_run_id=task.pipeline_task_run_id,
            pipeline_task_id=task.pipeline_task_id,
            created_at=now,
            updated_at=now,
        )


def _inputs(*, pipeline_run, task_definition, task_run) -> PipelineTaskInputs:
    return PipelineTaskInputs(
        pipeline=PipelineInputRef(
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_type=pipeline_run.type,
            task_id=task_definition.pipeline_task_id,
            pipeline_task_id=task_definition.pipeline_task_id,
            pipeline_task_run_id=task_run.pipeline_task_run_id,
        ),
        refs={"from": task_run.pipeline_task_id},
    )


def _resolver() -> MagicMock:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(side_effect=_inputs)
    return resolver


def _state(
    status: PipelineRunStatus = PipelineRunStatus.QUEUED, params: dict | None = None
) -> _State:
    now = utc_now()
    definition = get_pipeline_definition(PipelineType.RECORDING_SCENE_BUILDING)
    tasks = [
        PipelineTaskRunManifest(
            pipeline_task_run_id=f"ptr-{t.pipeline_task_id}",
            pipeline_run_id=RUN_ID,
            pipeline_task_id=t.pipeline_task_id,
            pipeline_task_name=t.name,
            task_order=t.order,
            status=PipelineTaskRunStatus.PENDING,
            job_type=t.job_type,
            depends_on_task_ids=t.depends_on_pipeline_task_ids,
            created_at=now,
            updated_at=now,
        )
        for t in definition.tasks
    ]
    run = PipelineRunManifest(
        pipeline_run_id=RUN_ID,
        type=PipelineType.RECORDING_SCENE_BUILDING,
        status=status,
        params=params or {},
        created_at=now,
        updated_at=now,
    )
    return _State(run, tasks)


class _Harness:
    def __init__(self, state: _State) -> None:
        self.state = state
        self.dispatcher = _dispatcher()
        self.planner = _Planner()
        self.orchestrator = PipelineOrchestrator(
            _context(state),
            dispatcher=self.dispatcher,
            planner=self.planner,
            input_resolver=_resolver(),
        )

    async def advance(self) -> PipelineRunManifest:
        return await self.orchestrator.advance(RUN_ID)

    def in_flight(self) -> JobManifest:
        (job,) = [j for j in self.state.jobs.values() if j.status == JobStatus.QUEUED]
        return job

    def settle(
        self, status: JobStatus, result: dict[str, Any] | None = None
    ) -> JobManifest:
        """Play the job worker: the in-flight Job reaches a terminal state."""
        job = self.in_flight()
        job.status = status
        job.result = result
        if status == JobStatus.FAILED:
            job.error = ErrorInfo(
                type="SceneBuildError", message="the recording is gone"
            )
        return job

    def task(self, task_id: str) -> PipelineTaskRunManifest:
        return self.state.tasks[task_id]


# ── submission ───────────────────────────────────────────────────────────────


async def test_starting_a_queued_run_submits_its_first_task_and_returns():
    h = _Harness(_state())

    run = await h.advance()

    assert run.status == PipelineRunStatus.RUNNING
    job = h.in_flight()
    assert job.pipeline_task_id == "build_recording_scenes"
    assert job.pipeline_run_id == RUN_ID
    assert h.task("build_recording_scenes").status == PipelineTaskRunStatus.RUNNING
    assert h.task("build_recording_scenes").job_id == job.job_id
    # Submitted to the job backend, recorded like an API dispatch, never executed.
    h.dispatcher.dispatch_job.assert_called_once_with(job.job_id)
    assert [e.resource_id for e in h.state.executions] == [job.job_id]
    assert all(
        h.task(t).status == PipelineTaskRunStatus.PENDING for t in SCENE_TASKS[1:]
    )


async def test_the_job_is_committed_before_it_is_dispatched():
    h = _Harness(_state())
    h.dispatcher.dispatch_job.side_effect = None

    def dispatch(job_id):
        assert (
            h.state.commits >= 1
        ), "a worker could claim a Job that does not exist yet"
        return ExecutionDispatchResult(
            execution_id="e",
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
        )

    h.dispatcher.dispatch_job.side_effect = dispatch
    await h.advance()
    h.dispatcher.dispatch_job.assert_called_once()


async def test_a_step_while_the_job_is_in_flight_changes_nothing():
    h = _Harness(_state())
    await h.advance()
    before = (_copy(h.state.run), {k: _copy(v) for k, v in h.state.tasks.items()})

    run = await h.advance()  # a duplicated or early report

    assert run.status == PipelineRunStatus.RUNNING
    assert (h.state.run, h.state.tasks) == before
    h.dispatcher.dispatch_job.assert_called_once()


# ── observation and advancement ──────────────────────────────────────────────


async def test_each_finished_job_advances_the_run_to_the_next_task_until_it_succeeds():
    h = _Harness(_state())
    await h.advance()

    h.settle(
        JobStatus.SUCCEEDED, {"manifest_artifact_ids": ["m-1"], "robot_run_id": "r"}
    )
    await h.advance()
    # The next task's Job carries what the previous one produced, via the resolver.
    assert h.task("build_recording_scenes").status == PipelineTaskRunStatus.SUCCEEDED
    assert h.task("build_recording_scenes").result.refs["manifest_artifact_ids"] == [
        "m-1"
    ]
    assert h.in_flight().pipeline_task_id == "register_scenes"

    h.settle(JobStatus.SUCCEEDED, {"scene_ids": ["s-1"]})
    await h.advance()
    assert h.in_flight().pipeline_task_id == "validate_scene"

    h.settle(JobStatus.SUCCEEDED, {"should_block_pipeline": False, "status": "passed"})
    run = await h.advance()

    # profile_scene is optional and the run names no params for it.
    assert h.task("profile_scene").status == PipelineTaskRunStatus.SKIPPED
    assert run.status == PipelineRunStatus.SUCCEEDED
    assert run.result.outputs["scene_ids"] == ["s-1"]
    assert run.result.summary["succeeded_task_count"] == 3
    assert run.result.summary["skipped_task_count"] == 1
    assert h.dispatcher.dispatch_job.call_count == 3


async def test_an_optional_task_with_params_runs_as_a_job():
    h = _Harness(_state(params={"profile_scene": {"triggered": True}}))
    await h.advance()
    for result in ({}, {}, {"should_block_pipeline": False}):
        h.settle(JobStatus.SUCCEEDED, result)
        await h.advance()

    assert h.in_flight().pipeline_task_id == "profile_scene"
    h.settle(JobStatus.SUCCEEDED, {})
    assert (await h.advance()).status == PipelineRunStatus.SUCCEEDED


async def test_a_failed_job_fails_its_task_and_the_run_with_the_jobs_error():
    h = _Harness(_state())
    await h.advance()

    h.settle(JobStatus.FAILED)
    run = await h.advance()

    task = h.task("build_recording_scenes")
    assert task.status == PipelineTaskRunStatus.FAILED
    assert task.error.type == "SceneBuildError"
    assert run.status == PipelineRunStatus.FAILED
    assert run.error == task.error
    assert h.dispatcher.dispatch_job.call_count == 1


async def test_a_quality_gate_blocks_the_task_and_the_run():
    h = _Harness(_state())
    await h.advance()
    for result in ({}, {}):
        h.settle(JobStatus.SUCCEEDED, result)
        await h.advance()

    h.settle(JobStatus.SUCCEEDED, {"should_block_pipeline": True})
    run = await h.advance()

    assert h.task("validate_scene").status == PipelineTaskRunStatus.BLOCKED
    assert h.task("validate_scene").error.type == "PipelineQualityBlocked"
    assert run.status == PipelineRunStatus.BLOCKED
    assert h.task("profile_scene").status == PipelineTaskRunStatus.PENDING


async def test_a_task_that_cannot_be_submitted_fails_the_run_and_dispatches_nothing():
    h = _Harness(_state())
    h.planner.fail_for = {"build_recording_scenes"}

    run = await h.advance()

    assert run.status == PipelineRunStatus.FAILED
    assert h.task("build_recording_scenes").status == PipelineTaskRunStatus.FAILED
    assert h.task("build_recording_scenes").job_id is None
    assert run.error.type == "ValueError"
    assert h.state.jobs == {}
    h.dispatcher.dispatch_job.assert_not_called()


# ── re-execution ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["failed", "blocked"])
async def test_a_requeued_run_resumes_at_its_unfinished_task_with_a_fresh_job(outcome):
    h = _Harness(_state())
    await h.advance()
    h.settle(JobStatus.SUCCEEDED, {})
    await h.advance()
    h.settle(JobStatus.SUCCEEDED, {})
    await h.advance()
    if outcome == "failed":
        h.settle(JobStatus.FAILED)
    else:
        h.settle(JobStatus.SUCCEEDED, {"should_block_pipeline": True})
    await h.advance()
    finished = {
        t: _copy(h.task(t)) for t in ("build_recording_scenes", "register_scenes")
    }
    first_validate_job = h.task("validate_scene").job_id

    h.state.run.status = PipelineRunStatus.QUEUED  # POST /pipelines/runs/{id}/execute
    run = await h.advance()

    assert run.status == PipelineRunStatus.RUNNING
    assert {
        t: h.task(t) for t in finished
    } == finished, "succeeded tasks are not re-run"
    resubmitted = h.in_flight()
    assert resubmitted.pipeline_task_id == "validate_scene"
    assert resubmitted.job_id != first_validate_job
    assert h.task("validate_scene").status == PipelineTaskRunStatus.RUNNING
    assert h.task("validate_scene").error is None


@pytest.mark.parametrize(
    "status",
    [
        PipelineRunStatus.PENDING,
        PipelineRunStatus.SUCCEEDED,
        PipelineRunStatus.FAILED,
        PipelineRunStatus.BLOCKED,
        PipelineRunStatus.CANCELLED,
    ],
)
async def test_a_run_that_is_neither_queued_nor_running_is_left_alone(status):
    """A late Job report never restarts a finished run; only an explicit execute
    (which queues the run) does."""
    h = _Harness(_state(status=status))

    run = await h.advance()

    assert run.status == status
    assert h.state.jobs == {}
    h.dispatcher.dispatch_job.assert_not_called()


async def test_every_step_locks_the_run():
    h = _Harness(_state())
    await h.advance()
    await h.advance()
    assert h.state.locked == [RUN_ID, RUN_ID]


async def test_an_unknown_run_is_an_error():
    h = _Harness(_state())
    with pytest.raises(FileNotFoundError):
        await h.orchestrator.advance("pipe-missing")


async def test_a_running_task_whose_job_vanished_fails_the_run():
    h = _Harness(_state())
    await h.advance()
    h.state.jobs.clear()

    run = await h.advance()

    assert run.status == PipelineRunStatus.FAILED
    assert h.task("build_recording_scenes").error.type == "PipelineTaskJobMissing"


def test_job_types_are_the_definitions():
    # Guards the fixture: the scene pipeline's tasks are the ones the tests walk.
    definition = get_pipeline_definition(PipelineType.RECORDING_SCENE_BUILDING)
    assert [t.pipeline_task_id for t in definition.tasks] == SCENE_TASKS
    assert definition.tasks[0].job_type == JobType.BUILD_RECORDING_SCENES
