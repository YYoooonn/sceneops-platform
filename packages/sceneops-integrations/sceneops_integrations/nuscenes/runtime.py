"""Maps the frozen ``IntegrationRequest``/``IntegrationResult`` contract
(SceneOps V2 Request 4.1/4.1A, ``sceneops_core.integration_runtime``) onto
``read_nuscenes_raw_log`` (Request 4.4).

::

    IntegrationRequest (operation=INGEST, external_ref.format="nuscenes")
            -> execute()
            -> read_nuscenes_raw_log()          (raw_log.py, SDK-bound)
            -> IntegrationResult.produced_artifacts
                 {"raw_log_manifest": ArtifactRef, "raw_log_frame_index": ArtifactRef}

Supports only ``operation=INGEST`` / ``external_ref.format="nuscenes"`` --
anything else is rejected clearly before any ``ArtifactStore`` access,
mirroring ``sceneops_analytics.external_adapters.lerobot.entrypoint``'s
``_check_supported`` for the EXPORT direction (Request 4.2).

Runtime ownership (Request 4.4 §4): this module may use the nuScenes SDK
(via ``raw_log.py``), ``sceneops-core``, and ``ArtifactStore``. It never
opens a DB session, never imports ``sceneops-db``, and never registers an
``ArtifactRecord`` or mutates ``DatasetVersion``/``Scene`` state -- the
worker (``BuildScenesJobHandler``, via ``NuScenesRawLogMocker``) remains
solely responsible for that, using this module's
``IntegrationResult.produced_artifacts``.

This module stops at an in-process ``execute()`` -- ``entrypoint.py``
(this same package) is the argv/stdin/stdout CLI/container wrapper around
it, the same way ``sceneops_analytics.external_adapters.lerobot.
entrypoint.execute`` is the testable core its own ``entrypoint.main``
wraps.

Destination URIs (``manifest_uri``/``frame_index_uri``) are explicit
``execute()`` parameters, not part of ``IntegrationRequest``: where a raw
log's artifacts land under a DatasetVersion's root is SceneOps' own
artifact-layout policy (``ObservationArtifactStore.raw_log_manifest_uri``/
``raw_frame_index_uri``, scoped by ``raw_log_id`` -- Request 22/F-01), owned
by the caller, never by the SDK-bound integration runtime -- matching
``IntegrationRequest.config``'s documented scope (opaque, integration
*parameter* configuration like ``max_source_sequences``, never artifact
storage layout).
"""

from __future__ import annotations

import hashlib

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.integration_runtime import (
    IntegrationOperation,
    IntegrationRequest,
    IntegrationResult,
)

from .raw_log import read_nuscenes_raw_log

SUPPORTED_OPERATION = IntegrationOperation.INGEST
SUPPORTED_FORMAT = "nuscenes"

RAW_LOG_MANIFEST_OUTPUT_KEY = "raw_log_manifest"
RAW_LOG_FRAME_INDEX_OUTPUT_KEY = "raw_log_frame_index"


class IntegrationRuntimeError(RuntimeError):
    """One request/execution this runtime refuses or fails to complete."""


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _check_supported(request: IntegrationRequest) -> None:
    if request.operation is not SUPPORTED_OPERATION:
        raise IntegrationRuntimeError(
            f"unsupported operation {request.operation.value!r}: this "
            f"runtime only executes operation={SUPPORTED_OPERATION.value!r}"
        )
    if request.external_ref.format != SUPPORTED_FORMAT:
        raise IntegrationRuntimeError(
            f"unsupported external_ref.format {request.external_ref.format!r}: "
            f"this runtime only executes format={SUPPORTED_FORMAT!r}"
        )
    if not request.external_ref.format_version:
        # No source-version fallback (SceneOps V2 Request 3.2B/3.2B.1):
        # never reuse canonical_ref.dataset_version as a stand-in for
        # nuScenes' own on-disk version folder name.
        raise IntegrationRuntimeError(
            "external_ref.format_version is required for a nuscenes "
            "INGEST -- refusing to fall back to "
            f"canonical_ref.dataset_version={request.canonical_ref.dataset_version!r}"
        )


async def execute(
    request: IntegrationRequest,
    *,
    artifact_store: ArtifactStore,
    raw_log_id: str,
    manifest_uri: str,
    frame_index_uri: str,
) -> IntegrationResult:
    """The testable core: given an already-validated ``IntegrationRequest``,
    an ``ArtifactStore`` (real or a ``LocalArtifactStore``/fake fixture),
    and the two destination URIs the caller has already resolved, run the
    nuScenes raw-log ingest and return the ``IntegrationResult``."""
    _check_supported(request)

    max_source_sequences = request.config.get("max_source_sequences")

    manifest, frame_index = await read_nuscenes_raw_log(
        artifact_store=artifact_store,
        source_root_uri=request.external_ref.uri,
        source_format_version=request.external_ref.format_version,
        dataset_id=request.canonical_ref.dataset_id,
        dataset_version=request.canonical_ref.dataset_version,
        raw_log_id=raw_log_id,
        manifest_uri=manifest_uri,
        frame_index_uri=frame_index_uri,
        max_source_sequences=max_source_sequences,
    )

    manifest_bytes = await artifact_store.read_bytes(manifest_uri)
    frame_index_bytes = await artifact_store.read_bytes(frame_index_uri)

    return IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=request.external_ref,
        canonical_ref=request.canonical_ref,
        produced_artifacts={
            RAW_LOG_MANIFEST_OUTPUT_KEY: ArtifactRef(
                kind=ArtifactKind.RAW_LOG_MANIFEST,
                uri=manifest_uri,
                media_type="application/json",
                checksum=_sha256_prefixed(manifest_bytes),
            ),
            RAW_LOG_FRAME_INDEX_OUTPUT_KEY: ArtifactRef(
                kind=ArtifactKind.RAW_LOG_FRAME_INDEX,
                uri=frame_index_uri,
                media_type="application/json",
                checksum=_sha256_prefixed(frame_index_bytes),
            ),
        },
        result_metadata={
            "raw_log_id": raw_log_id,
            "frame_count": manifest.frame_count,
            "calibration_count": manifest.calibration_count,
            "ego_pose_count": manifest.ego_pose_count,
            "sequence_count": manifest.sequence_count,
            "channels": manifest.channels,
        },
    )


__all__ = [
    "SUPPORTED_OPERATION",
    "SUPPORTED_FORMAT",
    "RAW_LOG_MANIFEST_OUTPUT_KEY",
    "RAW_LOG_FRAME_INDEX_OUTPUT_KEY",
    "IntegrationRuntimeError",
    "execute",
]
