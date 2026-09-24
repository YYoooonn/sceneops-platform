"""HTTP transport for the nuScenes INGEST integration runtime (SceneOps V2
Request 4.6A).

::

    POST /execute?raw_log_id=...&manifest_uri=...&frame_index_uri=...
    body: IntegrationRequest JSON
            -> execute()                      (runtime.py, unchanged)
            -> nuscenes-devkit read + ArtifactStore write
    <- IntegrationResult JSON (200) | error detail (4xx/5xx)

Replaces Docker-socket/CLI control (``ContainerIntegrationExecutor``,
Request 4.6) as the worker's normal production transport: the worker now
reaches this service as a plain internal HTTP endpoint
(``sceneops_worker.integration_execution.HttpIntegrationExecutor``) instead
of spawning it as a sibling container via the host's Docker daemon. The
container CLI entrypoint (``entrypoint.py``) is unchanged and still the
image's alternate ``docker run <image> -m
sceneops_integrations.nuscenes.entrypoint ...`` invocation, used by
``make nuscenes-container-smoke``/local debugging -- this module is the
image's new DEFAULT command (see ``tools/nuscenes-integration/Dockerfile``).

Runtime ownership (unchanged from Request 4.4/4.5/4.6): this service may
use the nuScenes SDK (via ``raw_log.py``), ``sceneops-core``/
``sceneops-storage``, and ``ArtifactStore`` access. It never opens a DB
session, never imports ``sceneops-db``/Celery, and never registers an
``ArtifactRecord`` or mutates ``DatasetVersion``/``Scene`` state -- the
worker remains solely responsible for that, using the ``IntegrationResult``
this endpoint returns. No job persistence, status table, or polling API is
added here -- ``POST /execute`` is synchronous: the HTTP response IS the
result, matching the container CLI's own one-shot semantics exactly.

ArtifactStore backend/credentials come entirely from environment variables
via the same ``NuScenesContainerSettings`` (``entrypoint.py``) the CLI
entrypoint already uses -- never duplicated or hardcoded here.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from sceneops_core.integration_runtime import IntegrationRequest, IntegrationResult

from .entrypoint import build_artifact_store
from .runtime import IntegrationRuntimeError, execute

logger = logging.getLogger(__name__)

app = FastAPI(
    title="nuscenes-integration",
    description="nuScenes INGEST IntegrationRequest -> IntegrationResult runtime",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Minimal liveness/readiness endpoint (Request 4.6A §6) -- no domain
    logic, no ArtifactStore/DB touch, just confirms the process is up.
    Used by compose's own ``healthcheck:``/``depends_on: condition:
    service_healthy`` (see ``compose/integrations.yaml``)."""
    return {"status": "ok"}


@app.post("/execute", response_model=IntegrationResult)
async def execute_endpoint(
    request: IntegrationRequest,
    raw_log_id: str = Query(
        ..., description="Caller-assigned raw log identity (scopes destination URIs)."
    ),
    manifest_uri: str = Query(
        ..., description="Destination ArtifactStore URI for the RawLogManifest."
    ),
    frame_index_uri: str = Query(
        ..., description="Destination ArtifactStore URI for the RawLogFrameIndex."
    ),
) -> IntegrationResult:
    """Synchronous execute: request in, result out, no intermediate state.
    ``raw_log_id``/``manifest_uri``/``frame_index_uri`` are query
    parameters rather than IntegrationRequest fields for the same reason
    they were separate CLI flags in ``entrypoint.py`` -- destination-URI
    layout is caller-owned policy, not part of the frozen contract
    (``runtime.py``'s own docstring)."""
    try:
        artifact_store = build_artifact_store()
        return await execute(
            request,
            artifact_store=artifact_store,
            raw_log_id=raw_log_id,
            manifest_uri=manifest_uri,
            frame_index_uri=frame_index_uri,
        )
    except IntegrationRuntimeError as exc:
        # A clear, expected runtime-level rejection (unsupported
        # operation/format, missing format_version, ...) -- surfaced as a
        # non-2xx response with a readable detail, never a 200 wrapping a
        # failure (Request 4.6A §7).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- always fail clearly, never leak a bare 500 with no detail
        logger.exception("nuscenes-integration /execute failed unexpectedly")
        raise HTTPException(
            status_code=500,
            detail=f"nuscenes integration service failed: {type(exc).__name__}: {exc}",
        ) from exc


__all__ = ["app"]
