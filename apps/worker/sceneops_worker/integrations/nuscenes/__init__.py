"""SDK-bound nuScenes INGEST integration runtime (SceneOps V2 Request 4.4).

::

    nuScenes SDK / source parsing        (raw_log.py)
            -> IntegrationResult.produced_artifacts   (runtime.py)
            -> main worker (NuScenesRawLogMocker, BuildScenesJobHandler)
            -> ArtifactRecord / Scene / DatasetVersion registration

See ``raw_log.py`` for the SDK-bound reader and ``runtime.py`` for the
``IntegrationRequest``/``IntegrationResult`` mapping. Neither depends on
``sceneops-db``, Celery, or worker job/context machinery.
"""

from .raw_log import infer_modality, is_object_storage_uri, read_nuscenes_raw_log
from .runtime import (
    RAW_LOG_FRAME_INDEX_OUTPUT_KEY,
    RAW_LOG_MANIFEST_OUTPUT_KEY,
    SUPPORTED_FORMAT,
    SUPPORTED_OPERATION,
    IntegrationRuntimeError,
    execute,
)

__all__ = [
    "infer_modality",
    "is_object_storage_uri",
    "read_nuscenes_raw_log",
    "RAW_LOG_FRAME_INDEX_OUTPUT_KEY",
    "RAW_LOG_MANIFEST_OUTPUT_KEY",
    "SUPPORTED_FORMAT",
    "SUPPORTED_OPERATION",
    "IntegrationRuntimeError",
    "execute",
]
