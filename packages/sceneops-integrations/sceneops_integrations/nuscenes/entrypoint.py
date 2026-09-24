"""Container/CLI entrypoint executing one frozen ``IntegrationRequest``
against ``read_nuscenes_raw_log`` and returning an ``IntegrationResult``
(SceneOps V2 Request 4.5).

::

    IntegrationRequest JSON (file)
            -> execute()                      (runtime.py)
            -> nuscenes-devkit read + ArtifactStore write
            -> IntegrationResult JSON (stdout, optionally also a file)

This module is the container's one entrypoint, not a second implementation
of anything Request 4.4 already froze: it only resolves argv/env/stdio into
a call against ``runtime.execute`` -- the same split
``sceneops_analytics.external_adapters.lerobot.entrypoint`` uses for the
EXPORT direction (Request 4.2).

Supports only ``operation=INGEST`` / ``external_ref.format="nuscenes"`` --
``runtime.execute`` rejects anything else clearly, before any
``ArtifactStore`` access.

Runtime ownership (Request 4.5 §5): this module may use the nuScenes SDK
(via ``raw_log.py``), ``sceneops-core``/``sceneops-storage``, and
``ArtifactStore`` access. It never opens a DB session, never imports
``sceneops-db``, and never registers an ``ArtifactRecord`` or mutates
``DatasetVersion``/``Scene`` state -- the main worker remains solely
responsible for that, using this process's
``IntegrationResult.produced_artifacts``.

Transport: a JSON ``IntegrationRequest`` file in (``--request-file``), a
JSON ``IntegrationResult`` on stdout on success (and optionally also
written to ``--output-file``) -- fully serializable, no DB objects or
in-process Python object sharing, trivial to invoke via ``docker run``.
Failure is a non-zero exit code plus a clear stderr message, never a caught
error serialized as "success" -- the same convention
``sceneops_analytics.external_adapters.lerobot.entrypoint`` and every
``scripts/e2e/e2e_lerobot_*.py`` script already use.

Destination URIs (``--raw-log-id``/``--manifest-uri``/``--frame-index-uri``)
are CLI arguments, not part of the ``IntegrationRequest`` JSON: per
``runtime.py``'s own docstring, where a raw log's artifacts land under a
DatasetVersion's root is SceneOps' own artifact-layout policy, owned by the
caller (the worker, or -- for the smoke test -- ``scripts/e2e/
nuscenes_container_build_request.py``), never by this SDK-bound container.

ArtifactStore backend/credentials are selected entirely from environment
variables, via the same ``pydantic_settings.BaseSettings`` +
``sceneops_core.config.ArtifactSettings`` pattern
``LeRobotContainerSettings``/``WorkerSettings``/``ApiSettings`` already use
-- never hardcoded to one backend (MinIO) in this nuScenes-specific module;
e.g.::

    SCENEOPS_INTEGRATION_ARTIFACT__BACKEND=minio
    SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI=s3://sceneops/artifacts
    SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL=http://minio:9000
    SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID=minioadmin
    SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY=minioadmin

This container never deletes or mutates ``external_ref.uri`` (the nuScenes
source dataroot) -- ``read_nuscenes_raw_log`` only ever reads from it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import ArtifactSettings
from sceneops_core.integration_runtime import IntegrationRequest, IntegrationResult
from sceneops_storage.factory import create_artifact_store

from .runtime import IntegrationRuntimeError, execute


class NuScenesContainerSettings(BaseSettings):
    """This container's only environment-driven configuration surface: the
    ArtifactStore backend. Reuses ``sceneops_core.config.ArtifactSettings``
    unchanged -- backend selection lives there and in
    ``sceneops_storage.factory.create_artifact_store``, never re-implemented
    here, exactly like ``LeRobotContainerSettings``/``WorkerSettings``/
    ``ApiSettings`` already do for the main platform and the LeRobot
    integration container."""

    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_INTEGRATION_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)


def _build_artifact_store():
    """Backend/credentials come entirely from ``NuScenesContainerSettings``
    (environment variables) -- never hardcoded here, never a field on
    ``IntegrationRequest``."""
    return create_artifact_store(NuScenesContainerSettings().artifact)


async def _run(
    *,
    request_file: Path,
    output_file: Path | None,
    raw_log_id: str,
    manifest_uri: str,
    frame_index_uri: str,
) -> IntegrationResult:
    try:
        payload = json.loads(request_file.read_text())
    except OSError as exc:
        raise IntegrationRuntimeError(f"cannot read --request-file: {exc}") from exc

    try:
        request = IntegrationRequest.model_validate(payload)
    except ValidationError as exc:
        raise IntegrationRuntimeError(f"invalid IntegrationRequest: {exc}") from exc

    artifact_store = _build_artifact_store()
    result = await execute(
        request,
        artifact_store=artifact_store,
        raw_log_id=raw_log_id,
        manifest_uri=manifest_uri,
        frame_index_uri=frame_index_uri,
    )

    result_json = result.model_dump_json(by_alias=True, indent=2)
    if output_file is not None:
        output_file.write_text(result_json)
    print(result_json)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request-file",
        required=True,
        type=Path,
        help="Path to a JSON IntegrationRequest (Request 4.1/4.1A shape, "
        "operation=INGEST/external_ref.format=nuscenes).",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional path to also write the JSON IntegrationResult "
        "(always printed to stdout on success).",
    )
    parser.add_argument(
        "--raw-log-id",
        required=True,
        help="Caller-assigned raw log identity (scopes the two destination "
        "URIs below, matching ObservationArtifactStore's convention).",
    )
    parser.add_argument(
        "--manifest-uri",
        required=True,
        help="Destination ArtifactStore URI for the RawLogManifest.",
    )
    parser.add_argument(
        "--frame-index-uri",
        required=True,
        help="Destination ArtifactStore URI for the RawLogFrameIndex.",
    )
    args = parser.parse_args(argv)

    try:
        asyncio.run(
            _run(
                request_file=args.request_file,
                output_file=args.output_file,
                raw_log_id=args.raw_log_id,
                manifest_uri=args.manifest_uri,
                frame_index_uri=args.frame_index_uri,
            )
        )
    except IntegrationRuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- top-level: always fail clearly
        traceback.print_exc(file=sys.stderr)
        print(
            f"❌ nuscenes integration container failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
