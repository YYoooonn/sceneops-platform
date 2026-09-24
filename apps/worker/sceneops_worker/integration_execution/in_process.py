"""``InProcessIntegrationExecutor``: an ``IntegrationExecutor`` that calls a
runtime's ``execute()`` function directly in the caller's own process
(SceneOps V2 Request 4.6 §4) -- tests and local dev only, never the
production path (``ContainerIntegrationExecutor``, ``container.py``).

Requires the target SDK to actually be importable in the caller's own
venv (e.g. a dev environment with ``nuscenes-devkit`` installed, or
``tools/nuscenes-integration``'s isolated venv) -- this class does not,
and the generic ``IntegrationExecutor`` contract never should, assume
that. It exists so tests can exercise the same ``IntegrationRequest`` ->
``IntegrationResult`` call shape the production path uses without paying
for a real ``docker run`` in every unit test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sceneops_core.integration_runtime import IntegrationRequest, IntegrationResult

_Runner = Callable[[IntegrationRequest], Awaitable[IntegrationResult]]


class InProcessIntegrationExecutor:
    """Wraps an already-bound runtime call (e.g.
    ``functools.partial(sceneops_integrations.nuscenes.runtime.execute,
    artifact_store=..., raw_log_id=..., manifest_uri=..., frame_index_uri=...)``)
    behind the ``IntegrationExecutor`` Protocol."""

    def __init__(self, run: _Runner) -> None:
        self._run = run

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return await self._run(request)


__all__ = ["InProcessIntegrationExecutor"]
