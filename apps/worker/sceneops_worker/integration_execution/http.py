"""``HttpIntegrationExecutor``: the production ``IntegrationExecutor``
backend (SceneOps V2 Request 4.6A) -- calls an integration runtime's
``POST /execute`` HTTP endpoint (``sceneops_integrations.nuscenes.service``,
or a future LeRobot service adopting the same transport) instead of
spawning it as a sibling Docker container
(``ContainerIntegrationExecutor``, ``container.py`` -- Request 4.6,
now a local/dev-only backend, see that module's own docstring).

::

    IntegrationRequest
            -> HttpIntegrationExecutor.execute()
            -> POST <base_url><path>?raw_log_id=...&manifest_uri=...&frame_index_uri=...
               body: IntegrationRequest JSON
            <- 200 IntegrationResult JSON | 4xx/5xx error detail
            -> IntegrationResult

Replaces Docker-socket/CLI control as the worker's normal production
transport (Request 4.6A §4/§6): the worker reaches the integration runtime
as a plain internal HTTP service on the SceneOps network, resolved by
service name/port (``HttpRuntimeConfig.base_url``), never by controlling
Docker. This removes the need for the worker container to have a `docker`
CLI or `/var/run/docker.sock` mount at all.

Interprets nothing format-specific: ``extra_query_params`` (the HTTP
equivalent of ``ContainerRuntimeConfig.extra_args``) is opaque, caller-
built configuration (``sceneops_worker.datasets.ingestion.nuscenes_raw_log``
for nuScenes) -- this module only knows how to attach it to the request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError
from sceneops_core.integration_runtime import IntegrationRequest, IntegrationResult

from .errors import IntegrationExecutionError


@dataclass(frozen=True)
class HttpRuntimeConfig:
    """Explicit, minimal descriptor for one HTTP integration-runtime
    invocation (Request 4.6A §3) -- built by the caller, never discovered
    through service discovery. ``base_url`` is the one piece of runtime
    routing (e.g. ``nuscenes -> http://nuscenes-integration:8080``,
    ``lerobot -> <future endpoint>``) -- an explicit mapping the caller
    owns, not a registry this class looks up."""

    base_url: str
    path: str = "/execute"
    extra_query_params: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 300.0


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return json.dumps(payload)


class HttpIntegrationExecutor:
    """Runs one ``IntegrationRequest`` against an integration runtime's
    ``POST /execute`` HTTP endpoint and returns the parsed, validated
    ``IntegrationResult``. See module docstring for the full contract."""

    def __init__(self, config: HttpRuntimeConfig) -> None:
        self._config = config

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        config = self._config
        url = f"{config.base_url.rstrip('/')}{config.path}"

        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response = await client.post(
                    url,
                    params=dict(config.extra_query_params),
                    content=request.model_dump_json(by_alias=True),
                    headers={"Content-Type": "application/json"},
                )
        except httpx.TransportError as exc:
            raise IntegrationExecutionError(
                f"cannot reach integration service at {url!r}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise IntegrationExecutionError(
                f"integration service {url!r} returned "
                f"{response.status_code}: {_error_detail(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrationExecutionError(
                f"integration service {url!r} returned invalid JSON: {exc}"
            ) from exc

        try:
            return IntegrationResult.model_validate(payload)
        except ValidationError as exc:
            raise IntegrationExecutionError(
                f"integration service {url!r} returned a payload that does "
                f"not validate as IntegrationResult: {exc}"
            ) from exc


__all__ = ["HttpRuntimeConfig", "HttpIntegrationExecutor"]
