"""Celery worker app for the lost-dispatch fault-injection tests.

It is the real worker (``sceneops_worker.celery_app``: same Celery config, same
``run_job_task`` / ``advance_pipeline_task``, ``JobRunner``, orchestrator and
dispatcher) with two additions:

* every Job type runs a probe handler whose result carries the outputs its
  pipeline task declares, so real PipelineRuns complete without real data;
* the dispatcher's two sends can be made to fail, by the fault file
  ``{point: [id or "*", ...]}`` read on every send:

      fail_dispatch_job   run_job(job_id) raises ConnectionError
      fail_advance        advance(pipeline_run_id) raises ConnectionError

  An injected failure raises where kombu raises when the broker is unreachable,
  after the durable state was committed.

Every probe run and every send (sent / failed) is appended to
``<marker_dir>/events.jsonl``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import sceneops_worker.execution.dispatcher as _dispatcher
import sceneops_worker.jobs.runner as _runner
import sceneops_worker.pipelines.planning as _planning
from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.builtin import BUILTIN_PIPELINE_DEFINITIONS
from sceneops_worker.celery_app import celery_app
from sceneops_worker.jobs.registry import JobHandlerRegistry

_CONTROL = Path(os.environ["RECOVERY_FAULT_FILE"])
_MARKERS = Path(os.environ["RECOVERY_MARKER_DIR"])
_NAME = os.environ["DISPATCH_WORKER_NAME"]


def event(name: str, key: str, **data) -> None:
    line = {"t": time.time(), "event": name, "id": key, "worker": _NAME, **data}
    with (_MARKERS / "events.jsonl").open("a") as fh:
        fh.write(json.dumps(line) + "\n")


def _faulted(point: str, key: str) -> bool:
    try:
        ids = json.loads(_CONTROL.read_text()).get(point, [])
    except FileNotFoundError:
        return False
    return "*" in ids or key in ids


def _set(result: dict, path: str, value) -> None:
    *parents, leaf = path.split(".")
    for part in parents:
        result = result.setdefault(part, {})
    result[leaf] = value


class _ProbeParams(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProbeHandler:
    params_model = _ProbeParams

    def __init__(self, job_type: JobType) -> None:
        self.job_type = job_type

    async def run(self, request):
        job = request.job
        event(
            "ran",
            job.job_id,
            generation=job.lease_generation,
            pipeline_run_id=job.pipeline_run_id,
        )
        result: dict = {}
        for definition in BUILTIN_PIPELINE_DEFINITIONS:
            for task in definition.tasks:
                if task.job_type != self.job_type or job.pipeline_task_id not in (
                    None,
                    task.pipeline_task_id,
                ):
                    continue
                for output in task.outputs:
                    if output.required and output.default is None:
                        _set(result, output.source, f"probe:{output.name}")
        return result

    def build_job_params(self, inputs):
        return {"probe": True}


def _registry(*_args, **_kwargs) -> JobHandlerRegistry:
    return JobHandlerRegistry([ProbeHandler(job_type) for job_type in JobType])


_real_dispatch_job = _dispatcher.CeleryExecutionDispatcher.dispatch_job
_real_advance = _dispatcher.CeleryExecutionDispatcher.advance_pipeline


def _dispatch_job(self, job_id):
    if _faulted("fail_dispatch_job", job_id):
        event("send_failed:run_job", job_id)
        raise ConnectionError("injected: broker unreachable (run_job)")
    result = _real_dispatch_job(self, job_id)
    event("sent:run_job", job_id)
    return result


def _advance(self, pipeline_run_id):
    if _faulted("fail_advance", pipeline_run_id):
        event("send_failed:advance", pipeline_run_id)
        raise ConnectionError("injected: broker unreachable (advance)")
    _real_advance(self, pipeline_run_id)
    event("sent:advance", pipeline_run_id)


_runner.create_default_job_handler_registry = _registry
_planning.create_default_job_handler_registry = _registry
_dispatcher.CeleryExecutionDispatcher.dispatch_job = _dispatch_job
_dispatcher.CeleryExecutionDispatcher.advance_pipeline = _advance

__all__ = ["celery_app"]
