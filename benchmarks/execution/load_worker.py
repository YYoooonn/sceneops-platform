"""Celery worker app for the execution observability experiments.

It is ``tests/infrastructure/dispatch_worker`` (the production worker: same Celery
config, tasks, JobRunner, lease keeper, orchestrator and dispatcher; every handler
a probe whose result carries its pipeline task's outputs; sends that a fault file
can fail) with a handler that takes time and can fail, both read from the JSON
control file ``LOAD_CONTROL_FILE`` on every Job, so a running experiment changes
them without restarting a worker::

    {"delay": {"<job type>": [median_s, sigma], "*": [median_s, sigma]},
     "fail":  {"<job type>": probability}}

A delay is log-normal around its median (``sigma`` 0 = constant), slept with
``asyncio.sleep`` like a handler awaiting I/O. A failure raises ``ValueError``
after the delay: a domain failure, persisted as a FAILED Job.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
from pathlib import Path

import dispatch_worker  # noqa: F401 - installs the probe registry and send faults
import sceneops_worker.jobs.runner as _runner
import sceneops_worker.pipelines.planning as _planning
from dispatch_worker import ProbeHandler, celery_app, event
from sceneops_core.jobs.schemas import JobType
from sceneops_worker.jobs.registry import JobHandlerRegistry

_CONTROL = Path(os.environ["LOAD_CONTROL_FILE"])


def _control() -> dict:
    try:
        return json.loads(_CONTROL.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _pick(table: dict, job_type: str, default):
    return table.get(job_type, table.get("*", default))


class LoadHandler(ProbeHandler):
    """Appends ``start`` (handler entered) to the marker log; ``ran`` (from the
    probe) marks the end of a successful handler."""

    async def run(self, request):
        job = request.job
        event("start", job.job_id, generation=job.lease_generation, type=job.type.value)
        control = _control()
        median, sigma = _pick(control.get("delay", {}), self.job_type.value, [0, 0])
        if median > 0:
            await asyncio.sleep(random.lognormvariate(math.log(median), sigma))
        if random.random() < _pick(control.get("fail", {}), self.job_type.value, 0):
            event("fail", job.job_id, generation=job.lease_generation)
            raise ValueError(f"injected domain failure ({self.job_type.value})")
        return await super().run(request)


def _registry(*_args, **_kwargs) -> JobHandlerRegistry:
    return JobHandlerRegistry([LoadHandler(job_type) for job_type in JobType])


_runner.create_default_job_handler_registry = _registry
_planning.create_default_job_handler_registry = _registry

__all__ = ["celery_app"]
