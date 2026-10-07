"""Celery worker app for the job-lease fault-injection tests.

It is the real worker (``sceneops_worker.celery_app``: same Celery config, same
``run_job_task``, same ``JobRunner``, lease keeper and fencing) with one addition:
``CURATE_EPISODES`` Jobs run a probe handler instead of the domain handler, so a
test can stop a worker at an exact point of a Job without touching real data.
The probe writes one write-once object (``liveness/<job_id>/output.json``), the
artifact a duplicate execution must converge on.

Points are read from the JSON fault file on every Job, ``{point: [job_id, ...]}``:

    in_handler       hold on entering the handler
    after_artifact   hold after the artifact write, before the handler returns
    block_loop       block the event loop (time.sleep) for LEASE_BLOCK_SECONDS

A hold appends ``hold:<point>`` to ``<marker_dir>/events.jsonl`` and waits until
``<marker_dir>/release.<job_id>.<worker name>`` exists. Every probe point, and the
end of each ``JobRunner.run``, appends a JSON line (time, worker, pid, worker_id)
so a test can tell which worker held which claim.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import sceneops_worker.jobs.runner as _runner
from sceneops_core.jobs.schemas import JobType
from sceneops_storage.write_once import write_once
from sceneops_worker.celery_app import celery_app
from sceneops_worker.jobs.registry import JobHandlerRegistry

_CONTROL = Path(os.environ["RECOVERY_FAULT_FILE"])
_MARKERS = Path(os.environ["RECOVERY_MARKER_DIR"])
_NAME = os.environ["LEASE_WORKER_NAME"]
PROBE_JOB_TYPE = JobType.CURATE_EPISODES

# A redelivery after a whole worker died needs a live worker to restore the
# message once it is older than the visibility timeout (kombu's default: 1 h).
if os.environ.get("LEASE_VISIBILITY_TIMEOUT"):
    celery_app.conf.broker_transport_options = {
        "visibility_timeout": float(os.environ["LEASE_VISIBILITY_TIMEOUT"])
    }


def _points(job_id: str) -> set[str]:
    try:
        control = json.loads(_CONTROL.read_text())
    except FileNotFoundError:
        return set()
    return {point for point, job_ids in control.items() if job_id in job_ids}


def event(name: str, job_id: str, **data) -> None:
    line = {
        "t": time.time(),
        "event": name,
        "job_id": job_id,
        "worker": _NAME,
        "pid": os.getpid(),
        **data,
    }
    with (_MARKERS / "events.jsonl").open("a") as fh:
        fh.write(json.dumps(line) + "\n")


async def _point(name: str, job_id: str, **data) -> None:
    event(name, job_id, **data)
    if name not in _points(job_id):
        return
    if name == "block_loop":
        time.sleep(float(os.environ["LEASE_BLOCK_SECONDS"]))
        return
    event(f"hold:{name}", job_id, **data)
    release = _MARKERS / f"release.{job_id}.{_NAME}"
    while not release.exists():
        await asyncio.sleep(0.05)


class _ProbeParams(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProbeHandler:
    job_type = PROBE_JOB_TYPE
    params_model = _ProbeParams

    async def run(self, request):
        job, context = request.job, request.context
        claim = {"worker_id": context.worker_id, "generation": job.lease_generation}
        await _point("in_handler", job.job_id, **claim)
        await _point("block_loop", job.job_id, **claim)
        written = await write_once(
            context.artifact_store,
            f"{context.settings.artifact.root_uri}/liveness/{job.job_id}/output.json",
            json.dumps({"job_id": job.job_id}).encode(),
        )
        await _point("after_artifact", job.job_id, created=written.created, **claim)
        return {"created": written.created}

    def build_job_params(self, inputs):
        return {}


_default_registry = _runner.create_default_job_handler_registry


def _registry() -> JobHandlerRegistry:
    handlers = [
        handler
        for handler in _default_registry()._handlers.values()
        if handler.job_type != PROBE_JOB_TYPE
    ]
    return JobHandlerRegistry([*handlers, ProbeHandler()])


_real_run = _runner.JobRunner.run


async def _run(self, job_id: str):
    try:
        job = await _real_run(self, job_id)
    except BaseException as exc:
        event("run_raised", job_id, worker_id=self.worker_id, error=repr(exc)[:300])
        raise
    event(
        "run_returned",
        job_id,
        worker_id=self.worker_id,
        status=job.status.value,
        generation=job.lease_generation,
    )
    return job


_runner.create_default_job_handler_registry = _registry
_runner.JobRunner.run = _run

__all__ = ["celery_app"]
