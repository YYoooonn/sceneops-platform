"""The Celery ``run_job`` task: a refused claim is a duplicate message, not a task
failure -- one WARNING line and a result, never an ERROR traceback -- while any
other exception still fails the task."""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import MagicMock

import pytest

import sceneops_worker.tasks.jobs as tasks
from sceneops_worker.jobs.runner import JobNotClaimableError


@pytest.fixture
def runner_raising(monkeypatch):
    def _install(error: Exception) -> None:
        @contextlib.asynccontextmanager
        async def _session_scope():
            yield MagicMock()

        class _Runner:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def run(self, job_id):
                raise error

        async def _dispose():
            return None

        monkeypatch.setattr(tasks, "async_session_scope", _session_scope)
        monkeypatch.setattr(tasks, "create_worker_context", MagicMock())
        monkeypatch.setattr(tasks, "create_execution_dispatcher", MagicMock())
        monkeypatch.setattr(tasks, "dispose_async_engine", _dispose)
        monkeypatch.setattr(tasks, "JobRunner", _Runner)

    return _install


def test_a_refused_claim_returns_not_claimed_and_logs_one_warning(
    runner_raising, caplog
):
    runner_raising(JobNotClaimableError("Job is already succeeded: job-1"))

    with caplog.at_level(logging.WARNING):
        result = tasks.run_job_task.apply(args=["job-1"])

    assert result.successful()
    assert result.result == {"job_id": "job-1", "status": "not_claimed"}
    refusals = [r for r in caplog.records if "job claim refused" in r.getMessage()]
    assert [r.levelno for r in refusals] == [logging.WARNING]


def test_any_other_error_still_fails_the_task(runner_raising):
    runner_raising(FileNotFoundError("Job not found: job-1"))

    result = tasks.run_job_task.apply(args=["job-1"])

    assert result.failed()
    assert isinstance(result.result, FileNotFoundError)
