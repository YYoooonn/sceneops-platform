"""The API's Celery backends must not hold the event loop during a broker send.

The fake broker's `send_task` returns only after a coroutine running concurrently on
the same loop has run. If the send blocked the loop, that coroutine could never run
and the send would time out.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from sceneops_core.constants.tasks import JOB_RUN_TASK, PIPELINE_ADVANCE_TASK
from sceneops_core.executions.schemas import ExecutionBackend, ExecutionKind
from sceneops_execution.executions.backends.celery import (
    CeleryJobExecutionBackend,
    CeleryPipelineExecutionBackend,
)

SEND_TIMEOUT_SECONDS = 2.0


class _BrokerWaitingOnLoop:
    def __init__(self) -> None:
        self.loop_ran = threading.Event()
        self.sent: list[dict] = []

    def send_task(self, name, *, args, queue, routing_key):
        if not self.loop_ran.wait(SEND_TIMEOUT_SECONDS):
            raise AssertionError("send_task blocked the event loop")
        self.sent.append(
            {"name": name, "args": args, "queue": queue, "routing_key": routing_key}
        )
        return SimpleNamespace(id="celery-task-1")


async def _mark_loop_ran(broker: _BrokerWaitingOnLoop) -> None:
    broker.loop_ran.set()


async def test_dispatch_job_sends_without_blocking_the_event_loop():
    broker = _BrokerWaitingOnLoop()
    backend = CeleryJobExecutionBackend(app=broker, job_queue="sceneops.jobs")

    result, _ = await asyncio.gather(
        backend.dispatch_job("job-1"), _mark_loop_ran(broker)
    )

    assert broker.sent == [
        {
            "name": JOB_RUN_TASK,
            "args": ["job-1"],
            "queue": "sceneops.jobs",
            "routing_key": "sceneops.jobs",
        }
    ]
    assert result.execution_id == "celery-task-1"
    assert result.execution_backend == ExecutionBackend.CELERY
    assert result.execution_kind == ExecutionKind.JOB_RUN
    assert result.resource_id == "job-1"


async def test_dispatch_pipeline_sends_without_blocking_the_event_loop():
    broker = _BrokerWaitingOnLoop()
    backend = CeleryPipelineExecutionBackend(
        app=broker, pipeline_queue="sceneops.pipeline_runs"
    )

    result, _ = await asyncio.gather(
        backend.dispatch_pipeline("run-1"), _mark_loop_ran(broker)
    )

    assert broker.sent == [
        {
            "name": PIPELINE_ADVANCE_TASK,
            "args": ["run-1"],
            "queue": "sceneops.pipeline_runs",
            "routing_key": "sceneops.pipeline_runs",
        }
    ]
    assert result.execution_kind == ExecutionKind.PIPELINE_RUN
    assert result.resource_id == "run-1"


async def test_send_failure_propagates_to_the_dispatcher():
    class _UnreachableBroker:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("broker unreachable")

    backend = CeleryJobExecutionBackend(app=_UnreachableBroker(), job_queue="q")

    with pytest.raises(ConnectionError, match="broker unreachable"):
        await backend.dispatch_job("job-1")
