from __future__ import annotations


class IntegrationExecutionError(RuntimeError):
    """One IntegrationExecutor.execute() call failed at the EXECUTION layer
    -- the runtime process/container could not be started, exited non-zero,
    wrote no result, or wrote something that doesn't validate as an
    IntegrationResult (SceneOps V2 Request 4.6 §7).

    Deliberately distinct from each runtime's own domain-level error type
    (e.g. ``sceneops_integrations.nuscenes.runtime.IntegrationRuntimeError``,
    raised *inside* the container/process): once a request crosses the
    process boundary, the executor can only observe a runtime's own
    rejection as a non-zero exit code plus stderr text -- it never gets the
    original exception back. This error wraps that observation (exit code,
    stderr) for the caller; it never re-raises or re-wraps a specific
    runtime's exception type, since the executor must stay ignorant of
    which runtime it ran.
    """
