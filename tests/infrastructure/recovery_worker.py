"""Celery worker app for the acquisition-recovery fault-injection tests.

It is the real worker (``sceneops_worker.celery_app``: same Celery config, same
``run_job_task``, same ``JobRunner``, same registration) with one addition: the
``REGISTER_ROBOT_RUN`` handler's call to ``register_robot_run`` is wrapped so a
test can put a fault at an exact point of the registration of a chosen run.
Nothing else is replaced, so job claiming, heartbeats, failure recording and
redelivery are the production behavior.

Faults are read from a JSON control file on every invocation, so a test changes
them without restarting the worker::

    {"hold_before_commit": [run_id, ...],   # block before R8 -> then killed (W8)
     "hold_after_commit":  [run_id, ...],   # R8 committed, then block (W9)
     "transient_error":    [run_id, ...],   # raise OSError (transient class)
     "permanent_error":    [run_id, ...]}   # raise RecordingVerificationError

A held registration drops ``<RECOVERY_MARKER_DIR>/<fault>.<run_id>`` first, so
the test knows the worker is at that point before it kills it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import sceneops_worker.jobs.robots.register_robot_run as _handler
from sceneops_worker.celery_app import celery_app
from sceneops_worker.robots import registration as _registration

_CONTROL = Path(os.environ["RECOVERY_FAULT_FILE"])
_MARKERS = Path(os.environ["RECOVERY_MARKER_DIR"])
_real_register = _handler.register_robot_run


def _faults(run_id: str) -> set[str]:
    try:
        control = json.loads(_CONTROL.read_text())
    except FileNotFoundError:
        return set()
    return {fault for fault, run_ids in control.items() if run_id in run_ids}


async def _hold(fault: str, run_id: str) -> None:
    (_MARKERS / f"{fault}.{run_id}").touch()
    await asyncio.sleep(3600)


async def _register_with_faults(*, context, manifest_uri, job_id=None):
    run_id = manifest_uri.rstrip("/").split("/")[-2]
    faults = _faults(run_id)
    if "hold_before_commit" in faults:
        await _hold("hold_before_commit", run_id)
    if "transient_error" in faults:
        raise OSError("injected transient failure")
    if "permanent_error" in faults:
        raise _registration.RecordingVerificationError("injected permanent failure")
    result = await _real_register(
        context=context, manifest_uri=manifest_uri, job_id=job_id
    )
    if "hold_after_commit" in faults:
        await _hold("hold_after_commit", run_id)
    return result


_handler.register_robot_run = _register_with_faults

__all__ = ["celery_app"]
