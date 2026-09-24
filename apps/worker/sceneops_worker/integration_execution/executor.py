"""The minimal generic execution interface (SceneOps V2 Request 4.6 §2):
how the worker runs one already-built ``IntegrationRequest`` against SOME
isolated integration runtime and gets back an ``IntegrationResult``,
without knowing which SDK, container image, or CLI transport that runtime
uses.

::

    Integration implementation  -> what nuScenes/LeRobot does
                                    (packages/sceneops-integrations,
                                    sceneops_analytics.external_adapters.lerobot,
                                    each with its own container)

    Integration executor        -> HOW that implementation is run
                                    (this module + container.py/in_process.py)

    Main platform                -> WHY it is run, and how results are
                                    registered (sceneops_worker.jobs.*)

Mirrors ``sceneops_worker.observations.adapters.base.RawLogAdapter``'s own
shape (a ``runtime_checkable`` ``Protocol`` with one async method) --
matches this repository's existing convention for a small, swappable
execution seam instead of introducing a class hierarchy or plugin registry.

Only one thing is genuinely shared across every integration runtime
(Request 4.6 §1): given a serialized ``IntegrationRequest``, run it
somewhere isolated, and get back a validated ``IntegrationResult`` or a
clear failure. Everything format-specific -- which extra CLI args a
runtime needs, which artifact keys it produces, what its config dict
means -- stays entirely outside this interface, built by the caller
(``sceneops_worker.datasets.ingestion.nuscenes_ingestion`` for nuScenes)
before ``execute()`` is ever called.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.integration_runtime import IntegrationRequest, IntegrationResult


@runtime_checkable
class IntegrationExecutor(Protocol):
    """Runs one ``IntegrationRequest`` and returns its ``IntegrationResult``.

    Implementations decide HOW (``ContainerIntegrationExecutor`` -- the
    production boundary, Request 4.6 §4 -- or ``InProcessIntegrationExecutor``
    -- tests/local dev only). Neither this Protocol nor its callers need to
    know which."""

    async def execute(self, request: IntegrationRequest) -> IntegrationResult: ...


__all__ = ["IntegrationExecutor"]
