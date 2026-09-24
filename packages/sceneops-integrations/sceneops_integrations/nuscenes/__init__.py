"""SDK-bound nuScenes INGEST integration runtime (SceneOps V2 Request 4.4,
isolated into its own workspace package + container in Request 4.5, HTTP
transport in Request 4.6A, direct SceneManifest ingest migrated in
Request 4.6B).

::

    nuScenes SDK / source parsing
            raw_log.py       (mode=raw_log)      -> raw_log_manifest/raw_log_frame_index
            scene_ingest.py  (mode=scene_manifest) -> scene_manifest:<scene_id> (with GT annotations)
                    -> IntegrationResult.produced_artifacts   (runtime.py)
                    -> CLI/HTTP transport      (entrypoint.py / service.py)
                    -> main worker (BuildScenesJobHandler / IngestScenesJobHandler)
                    -> ArtifactRecord / Scene / DatasetVersion registration

See ``raw_log.py``/``scene_ingest.py`` for the two SDK-bound readers,
``runtime.py`` for the ``IntegrationRequest``/``IntegrationResult`` mapping
(mode dispatch), ``entrypoint.py`` for the container's CLI wrapper, and
``service.py`` for its HTTP wrapper
(``tools/nuscenes-integration/Dockerfile``). None depend on
``sceneops-db``, Celery, or worker job/context machinery.
"""

from .raw_log import infer_modality, is_object_storage_uri, read_nuscenes_raw_log
from .runtime import (
    MODE_RAW_LOG,
    MODE_SCENE_MANIFEST,
    RAW_LOG_FRAME_INDEX_OUTPUT_KEY,
    RAW_LOG_MANIFEST_OUTPUT_KEY,
    SCENE_MANIFEST_OUTPUT_KEY_PREFIX,
    SUPPORTED_FORMAT,
    SUPPORTED_OPERATION,
    IntegrationRuntimeError,
    execute,
)
from .scene_ingest import IngestedScene, build_scene_manifest, ingest_nuscenes_scenes

__all__ = [
    "infer_modality",
    "is_object_storage_uri",
    "read_nuscenes_raw_log",
    "IngestedScene",
    "build_scene_manifest",
    "ingest_nuscenes_scenes",
    "MODE_RAW_LOG",
    "MODE_SCENE_MANIFEST",
    "RAW_LOG_FRAME_INDEX_OUTPUT_KEY",
    "RAW_LOG_MANIFEST_OUTPUT_KEY",
    "SCENE_MANIFEST_OUTPUT_KEY_PREFIX",
    "SUPPORTED_FORMAT",
    "SUPPORTED_OPERATION",
    "IntegrationRuntimeError",
    "execute",
]
