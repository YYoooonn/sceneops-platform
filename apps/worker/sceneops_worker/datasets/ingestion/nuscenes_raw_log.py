"""nuScenes raw-log ingest, routed through the generic execution model
(SceneOps V2 Request 4.6, HTTP transport in Request 4.6A).

::

    BuildScenesJobHandler
            -> build_nuscenes_ingest_request()   (this module)
            -> build_nuscenes_http_config()       (this module, PRODUCTION)
            -> HttpIntegrationExecutor.execute()
            -> nuscenes-integration HTTP service
            -> IntegrationResult

    (build_nuscenes_container_config() -- LOCAL/DEV ONLY, see its own
    docstring -- builds the ContainerIntegrationExecutor equivalent
    instead, used by make nuscenes-container-smoke and direct runtime
    debugging, not by BuildScenesJobHandler since Request 4.6A.)

This module builds the inputs a generic ``IntegrationExecutor`` needs for
one nuScenes INGEST call -- it contains no nuScenes SDK code itself (that
lives entirely in ``sceneops_integrations.nuscenes``/
``tools/nuscenes-integration``, Request 4.4/4.5) and no execution
mechanics (that's ``sceneops_worker.integration_execution``, Request
4.6/4.6A §2). It is the "why"/"what" boundary the module docstring in
``sceneops_worker.integration_execution.executor`` describes: this is
where the worker's own params/DatasetVersionRecord get turned into a
format-specific ``IntegrationRequest`` + runtime invocation config, not
where that request is either executed or interpreted.

Replaces the Request 4.4/4.5 transitional ``NuScenesRawLogMocker`` (an
in-process ``RawLogAdapter`` wrapper calling
``sceneops_integrations.nuscenes`` directly inside the worker process) --
removed in Request 4.6. No second in-process production path is kept as a
fallback; ``sceneops_integrations.nuscenes.runtime.execute`` remains
directly callable for tests (see ``InProcessIntegrationExecutor``) and is
what both the container entrypoint and the HTTP service (``service.py``)
call underneath, unchanged.
"""

from __future__ import annotations

from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)
from sceneops_worker.config import WorkerSettings
from sceneops_worker.integration_execution import (
    ContainerRuntimeConfig,
    HttpRuntimeConfig,
    artifact_settings_to_env,
)

NUSCENES_FORMAT = "nuscenes"


def build_nuscenes_ingest_request(
    *,
    dataset_id: str,
    dataset_version: str,
    source_root_uri: str,
    source_format_version: str,
    max_source_sequences: int | None = None,
) -> IntegrationRequest:
    """Build the frozen ``IntegrationRequest`` (Request 4.1/4.1A) for one
    nuScenes INGEST call. ``source_format_version`` is nuScenes' own
    on-disk version folder name (e.g. ``"v1.0-mini"``) -- the caller is
    responsible for resolving it (never ``dataset_version``, SceneOps' own
    canonical identity, per Request 3.2B/3.2B.1) and for rejecting an
    empty/missing value before calling this function; the container itself
    also refuses an empty ``external_ref.format_version`` as defense in
    depth (``sceneops_integrations.nuscenes.runtime._check_supported``).
    """
    config: dict = {}
    if max_source_sequences is not None:
        config["max_source_sequences"] = max_source_sequences

    return IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format=NUSCENES_FORMAT,
            format_version=source_format_version,
            uri=source_root_uri,
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id=dataset_id, dataset_version=dataset_version
        ),
        config=config,
    )


def build_nuscenes_http_config(
    *,
    settings: WorkerSettings,
    raw_log_id: str,
    manifest_uri: str,
    frame_index_uri: str,
) -> HttpRuntimeConfig:
    """Build the ``HttpRuntimeConfig`` to call the nuScenes integration
    HTTP service for one raw log (SceneOps V2 Request 4.6A -- the
    PRODUCTION path). ``base_url`` is explicit routing configuration from
    ``settings.integration_execution.nuscenes_service_url`` (Request 4.6A
    §3), resolved by service name on the SceneOps network
    (``compose/integrations.yaml``) -- never by controlling Docker.
    ``raw_log_id``/``manifest_uri``/``frame_index_uri`` become the
    ``/execute`` call's query parameters -- destination-URI layout stays
    caller-owned policy, exactly as it was as CLI flags for
    ``ContainerRuntimeConfig.extra_args`` below."""
    return HttpRuntimeConfig(
        base_url=settings.integration_execution.nuscenes_service_url,
        extra_query_params={
            "raw_log_id": raw_log_id,
            "manifest_uri": manifest_uri,
            "frame_index_uri": frame_index_uri,
        },
    )


def build_nuscenes_container_config(
    *,
    settings: WorkerSettings,
    raw_log_id: str,
    manifest_uri: str,
    frame_index_uri: str,
) -> ContainerRuntimeConfig:
    """Build the ``ContainerRuntimeConfig`` to run the isolated
    ``nuscenes-integration`` image for one raw log -- LOCAL/DEV ONLY since
    Request 4.6A (``make nuscenes-container-smoke``, direct runtime
    debugging); ``BuildScenesJobHandler`` no longer calls this (see
    ``build_nuscenes_http_config`` above for the production path). Image/
    network selection come from ``settings.integration_execution``
    (explicit configuration, Request 4.6 §8) -- credentials for the
    container's OWN ArtifactStore are translated from this worker's own
    ``settings.artifact`` (never duplicated/hardcoded here).

    Requires ``settings.integration_execution.host_data_root`` to be
    configured: `docker run -v host:container` is resolved by the Docker
    daemon against the HOST filesystem, never this (already-containerized)
    worker's own view of it, even though the worker already sees
    ``source_root_uri``/the exchange directory locally under ``/data``
    (Docker-outside-of-Docker -- see
    ``sceneops_core.config.IntegrationExecutionSettings``'s own docstring).
    """
    integration = settings.integration_execution
    if not integration.host_data_root:
        raise ValueError(
            "settings.integration_execution.host_data_root "
            "(SCENEOPS_WORKER_INTEGRATION_EXECUTION__HOST_DATA_ROOT) is "
            "required to run the nuScenes integration container -- it is "
            "the HOST-visible path backing this worker's own /data mount, "
            "needed so the sibling container's volume mount actually "
            "resolves to the same files this worker sees under /data."
        )

    io_dir = f"{integration.io_root_uri}/{raw_log_id}"

    return ContainerRuntimeConfig(
        image=integration.nuscenes_image,
        io_dir=io_dir,
        volumes=((integration.host_data_root, "/data"),),
        network=integration.docker_network,
        extra_args=(
            "--raw-log-id",
            raw_log_id,
            "--manifest-uri",
            manifest_uri,
            "--frame-index-uri",
            frame_index_uri,
        ),
        env=artifact_settings_to_env(settings.artifact),
    )


__all__ = [
    "NUSCENES_FORMAT",
    "build_nuscenes_ingest_request",
    "build_nuscenes_http_config",
    "build_nuscenes_container_config",
]
