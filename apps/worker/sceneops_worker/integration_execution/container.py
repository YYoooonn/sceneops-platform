"""``ContainerIntegrationExecutor``: a LOCAL/DEV ``IntegrationExecutor``
backend (introduced as the production path in SceneOps V2 Request 4.6,
demoted in Request 4.6A once ``HttpIntegrationExecutor`` -- ``http.py`` --
became the worker's normal production boundary). Runs one
``IntegrationRequest`` through an isolated ``docker run`` container,
exactly the way ``scripts/e2e/nuscenes_container_smoke.sh``/
``lerobot_container_smoke.sh`` already do by hand (Request 4.2/4.5), just
invoked from Python instead of bash.

Still useful for direct local runtime testing/debugging and
``make nuscenes-container-smoke`` -- but the worker no longer calls this
class during real job execution (Request 4.6A §4/§5): that required the
worker container to have a `docker` CLI and a `/var/run/docker.sock`
bind mount (Docker-outside-of-Docker, granting the worker root-equivalent
host access), which Request 4.6A removes.

Docker-specific by design (``ContainerRuntimeConfig``'s ``volumes``/
``network`` only mean anything to a Docker backend) -- deliberately kept
out of ``IntegrationRequest``/``IntegrationResult`` themselves (Request
4.6 §8), so a later executor for a different backend (Kubernetes, a bare
subprocess, ...) implements the same ``IntegrationExecutor`` Protocol with
its own config shape, untouched by anything here.

Never opens a DB session, never registers an ``ArtifactRecord`` -- purely
a process-execution wrapper. The container it runs is itself already
DB-free (Request 4.2/4.5's own containers) and this class does not change
that boundary, it only crosses it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sceneops_core.config import ArtifactSettings
from sceneops_core.integration_runtime import IntegrationRequest, IntegrationResult

from .errors import IntegrationExecutionError

_ARTIFACT_ENV_PREFIX = "SCENEOPS_INTEGRATION_ARTIFACT__"


def artifact_settings_to_env(
    settings: ArtifactSettings, *, prefix: str = _ARTIFACT_ENV_PREFIX
) -> dict[str, str]:
    """Translate an ``ArtifactSettings`` into the
    ``SCENEOPS_INTEGRATION_ARTIFACT__*`` environment variables every
    integration container's own ``pydantic_settings.BaseSettings`` expects
    (``NuScenesContainerSettings``/``LeRobotContainerSettings`` -- Request
    4.2/4.5, same env-driven ``ArtifactSettings``/``create_artifact_store``
    pattern the main platform's own ``WorkerSettings``/``ApiSettings``
    already use). One genuinely shared execution concern (Request 4.6 §1)
    -- never duplicated per integration.

    Only non-``None`` fields are included, so a container's own field
    defaults (e.g. no ``endpoint_url`` for a local backend) are never
    silently overridden with the literal string ``"None"``.
    """
    values: dict[str, str | None] = {
        "BACKEND": settings.backend.value,
        "ROOT_URI": settings.root_uri,
        "ENDPOINT_URL": settings.endpoint_url,
        "REGION": settings.region,
        "ACCESS_KEY_ID": settings.access_key_id,
        "SECRET_ACCESS_KEY": settings.secret_access_key,
    }
    return {f"{prefix}{k}": v for k, v in values.items() if v is not None}


@dataclass(frozen=True)
class ContainerRuntimeConfig:
    """Explicit, minimal descriptor for one containerized integration
    runtime invocation (Request 4.6 §3/§8) -- built by the caller (e.g.
    ``sceneops_worker.datasets.ingestion.nuscenes_raw_log`` for nuScenes),
    never discovered or dispatched through a generic registry. This is
    configuration, not a plugin framework.

    ``io_dir`` is where the request/result JSON files are exchanged --
    a path the WORKER process can write/read directly (it must already be
    visible on the worker's own filesystem) and that also resolves, inside
    the launched container, to the same files -- which ``volumes`` is
    responsible for making true (see
    ``sceneops_worker.datasets.ingestion.nuscenes_raw_log`` for how the
    nuScenes case satisfies this via a single ``host_data_root:/data``
    mount, mirroring this worker's own ``./data:/data`` compose mount --
    Docker-outside-of-Docker: a sibling container's bind mount is resolved
    by the Docker daemon against the HOST filesystem, never the calling
    container's view of it, even when both happen to already see the same
    content).
    """

    image: str
    io_dir: str
    volumes: tuple[tuple[str, str], ...] = ()
    network: str | None = None
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


class ContainerIntegrationExecutor:
    """Runs one ``IntegrationRequest`` through ``docker run <config.image>``
    and returns the parsed, validated ``IntegrationResult``. See module
    docstring for the full contract."""

    def __init__(self, config: ContainerRuntimeConfig) -> None:
        self._config = config

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        config = self._config
        io_path = Path(config.io_dir)
        io_path.mkdir(parents=True, exist_ok=True)
        request_file = io_path / "request.json"
        output_file = io_path / "result.json"
        request_file.write_text(request.model_dump_json(by_alias=True))

        cmd = ["docker", "run", "--rm"]
        if config.network:
            cmd += ["--network", config.network]
        for key, value in config.env.items():
            cmd += ["-e", f"{key}={value}"]
        for host_path, container_path in config.volumes:
            cmd += ["-v", f"{host_path}:{container_path}"]
        cmd.append(config.image)
        cmd += ["--request-file", str(request_file), "--output-file", str(output_file)]
        cmd += list(config.extra_args)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise IntegrationExecutionError(
                f"cannot invoke docker to run integration container "
                f"{config.image!r}: {exc}"
            ) from exc

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            detail = (
                stderr.decode(errors="replace").strip()
                or stdout.decode(errors="replace").strip()
            )
            raise IntegrationExecutionError(
                f"integration container {config.image!r} exited "
                f"{proc.returncode}: {detail}"
            )

        if not output_file.exists():
            raise IntegrationExecutionError(
                f"integration container {config.image!r} exited 0 but wrote "
                f"no --output-file result at {output_file}"
            )

        try:
            payload = json.loads(output_file.read_text())
        except json.JSONDecodeError as exc:
            raise IntegrationExecutionError(
                f"integration container {config.image!r} wrote invalid JSON "
                f"to {output_file}: {exc}"
            ) from exc

        try:
            return IntegrationResult.model_validate(payload)
        except ValidationError as exc:
            raise IntegrationExecutionError(
                f"integration container {config.image!r} wrote a payload "
                f"that does not validate as IntegrationResult: {exc}"
            ) from exc


__all__ = [
    "ContainerRuntimeConfig",
    "ContainerIntegrationExecutor",
    "artifact_settings_to_env",
]
