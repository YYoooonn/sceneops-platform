"""Maps the frozen ``IntegrationRequest``/``IntegrationResult`` contract
(SceneOps V2 Request 4.1/4.1A, ``sceneops_core.integration_runtime``) onto
this package's two nuScenes INGEST capabilities:

::

    IntegrationRequest (operation=INGEST, external_ref.format="nuscenes")
            -> execute()
            -> config["mode"] == "raw_log" (default)
                    -> read_nuscenes_raw_log()      (raw_log.py, Request 4.4)
                    -> produced_artifacts: {"raw_log_manifest", "raw_log_frame_index"}
            -> config["mode"] == "scene_manifest"
                    -> ingest_nuscenes_scenes()      (scene_ingest.py, Request 4.6B)
                    -> produced_artifacts: {"scene_manifest:<scene_id>", ...}

``scene_manifest`` mode (Request 4.6B) migrated what was previously the
worker's own direct in-process ``IngestScenesJobHandler``/
``_ingest_nuscenes_scenes`` call -- a genuinely distinct capability from
``raw_log`` mode (it produces ground-truth-annotated ``SceneManifest``s
directly from nuScenes' native scenes, no raw-log/``BUILD_SCENES``
segmentation involved at all), not a duplicate of it. Reusing this same
``execute()``/HTTP transport for both keeps one integration boundary
instead of adding a second one.

Supports only ``operation=INGEST`` / ``external_ref.format="nuscenes"`` --
anything else is rejected clearly before any ``ArtifactStore`` access,
mirroring ``sceneops_analytics.external_adapters.lerobot.entrypoint``'s
``_check_supported`` for the EXPORT direction (Request 4.2).

Runtime ownership (Request 4.4 §4, unchanged in 4.6B): this module may use
the nuScenes SDK (via ``raw_log.py``/``scene_ingest.py``), ``sceneops-core``,
and ``ArtifactStore``. It never opens a DB session, never imports
``sceneops-db``, and never registers an ``ArtifactRecord`` or mutates
``DatasetVersion``/``Scene`` state -- the worker
(``BuildScenesJobHandler``/``IngestScenesJobHandler``) remains solely
responsible for that, using this module's
``IntegrationResult.produced_artifacts``.

This module stops at an in-process ``execute()`` -- ``entrypoint.py``/
``service.py`` (this same package) are the CLI/HTTP wrappers around it.

Destination URIs (``manifest_uri``/``frame_index_uri``/
``scene_manifest_root_uri``) are explicit ``execute()`` parameters, not
part of ``IntegrationRequest``: where an artifact lands under a
DatasetVersion's root is SceneOps' own artifact-layout policy
(``ObservationArtifactStore``/``SceneArtifactStore``, owned by the caller,
never by the SDK-bound integration runtime -- matching
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
from .scene_ingest import ingest_nuscenes_scenes

SUPPORTED_OPERATION = IntegrationOperation.INGEST
SUPPORTED_FORMAT = "nuscenes"

MODE_RAW_LOG = "raw_log"
MODE_SCENE_MANIFEST = "scene_manifest"
_SUPPORTED_MODES = {MODE_RAW_LOG, MODE_SCENE_MANIFEST}

RAW_LOG_MANIFEST_OUTPUT_KEY = "raw_log_manifest"
RAW_LOG_FRAME_INDEX_OUTPUT_KEY = "raw_log_frame_index"
SCENE_MANIFEST_OUTPUT_KEY_PREFIX = "scene_manifest"


class IntegrationRuntimeError(RuntimeError):
    """One request/execution this runtime refuses or fails to complete."""


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _check_supported(request: IntegrationRequest) -> str:
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

    mode = request.config.get("mode", MODE_RAW_LOG)
    if mode not in _SUPPORTED_MODES:
        raise IntegrationRuntimeError(
            f"unsupported config['mode'] {mode!r}: this runtime only "
            f"executes mode in {sorted(_SUPPORTED_MODES)!r}"
        )
    return mode


async def execute(
    request: IntegrationRequest,
    *,
    artifact_store: ArtifactStore,
    raw_log_id: str | None = None,
    manifest_uri: str | None = None,
    frame_index_uri: str | None = None,
    scene_manifest_root_uri: str | None = None,
) -> IntegrationResult:
    """The testable core: given an already-validated ``IntegrationRequest``,
    an ``ArtifactStore`` (real or a ``LocalArtifactStore``/fake fixture),
    and whichever destination URIs the chosen ``config["mode"]`` needs
    (already resolved by the caller), run the nuScenes ingest and return
    the ``IntegrationResult``. Which destination params are required
    depends on the mode -- see ``_execute_raw_log``/
    ``_execute_scene_manifest``."""
    mode = _check_supported(request)

    if mode == MODE_RAW_LOG:
        return await _execute_raw_log(
            request,
            artifact_store=artifact_store,
            raw_log_id=raw_log_id,
            manifest_uri=manifest_uri,
            frame_index_uri=frame_index_uri,
        )

    return await _execute_scene_manifest(
        request,
        artifact_store=artifact_store,
        scene_manifest_root_uri=scene_manifest_root_uri,
    )


async def _execute_raw_log(
    request: IntegrationRequest,
    *,
    artifact_store: ArtifactStore,
    raw_log_id: str | None,
    manifest_uri: str | None,
    frame_index_uri: str | None,
) -> IntegrationResult:
    if not raw_log_id or not manifest_uri or not frame_index_uri:
        raise IntegrationRuntimeError(
            "mode='raw_log' requires raw_log_id, manifest_uri, and " "frame_index_uri"
        )

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


async def _execute_scene_manifest(
    request: IntegrationRequest,
    *,
    artifact_store: ArtifactStore,
    scene_manifest_root_uri: str | None,
) -> IntegrationResult:
    if not scene_manifest_root_uri:
        raise IntegrationRuntimeError(
            "mode='scene_manifest' requires scene_manifest_root_uri"
        )

    source_scene_ids = request.config.get("source_scene_ids")
    max_source_scenes = request.config.get("max_source_scenes")

    ingested = await ingest_nuscenes_scenes(
        artifact_store=artifact_store,
        source_root_uri=request.external_ref.uri,
        source_format_version=request.external_ref.format_version,
        dataset_id=request.canonical_ref.dataset_id,
        dataset_version=request.canonical_ref.dataset_version,
        scene_manifest_root_uri=scene_manifest_root_uri,
        source_scene_ids=source_scene_ids,
        max_source_scenes=max_source_scenes,
    )

    produced_artifacts: dict[str, ArtifactRef] = {}
    total_samples = 0
    total_frames = 0
    all_channels: set[str] = set()

    for scene in ingested:
        manifest_bytes = await artifact_store.read_bytes(scene.manifest_uri)
        produced_artifacts[f"{SCENE_MANIFEST_OUTPUT_KEY_PREFIX}:{scene.scene_id}"] = (
            ArtifactRef(
                kind=ArtifactKind.SCENE_MANIFEST,
                uri=scene.manifest_uri,
                media_type="application/json",
                checksum=_sha256_prefixed(manifest_bytes),
                metadata={"scene_id": scene.scene_id},
            )
        )
        total_samples += scene.manifest.sample_count
        total_frames += scene.manifest.frame_count
        all_channels.update(scene.manifest.channels)

    if not produced_artifacts:
        raise IntegrationRuntimeError(
            "mode='scene_manifest' ingested zero scenes -- refusing to "
            "return an INGEST result with no produced_artifacts"
        )

    return IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=request.external_ref,
        canonical_ref=request.canonical_ref,
        produced_artifacts=produced_artifacts,
        result_metadata={
            "scene_ids": [scene.scene_id for scene in ingested],
            "scene_count": len(ingested),
            "sample_count": total_samples,
            "frame_count": total_frames,
            "channels": sorted(all_channels),
        },
    )


__all__ = [
    "SUPPORTED_OPERATION",
    "SUPPORTED_FORMAT",
    "MODE_RAW_LOG",
    "MODE_SCENE_MANIFEST",
    "RAW_LOG_MANIFEST_OUTPUT_KEY",
    "RAW_LOG_FRAME_INDEX_OUTPUT_KEY",
    "SCENE_MANIFEST_OUTPUT_KEY_PREFIX",
    "IntegrationRuntimeError",
    "execute",
]
