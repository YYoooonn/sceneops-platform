from __future__ import annotations

from enum import StrEnum


class IntegrationOperation(StrEnum):
    """Direction of one external-integration-runtime execution (SceneOps V2
    Request 4.1 §3). ``ExternalDatasetRef`` deliberately has one shape for
    both an import source and an export target (Request 3.2B) -- direction
    belongs to the operation invoking it, never to the ref itself. This is
    that operation.

    INGEST   ExternalDatasetRef -> SceneOps canonical (e.g. nuScenes -> SceneOps)
    EXPORT   SceneOps canonical -> ExternalDatasetRef (e.g. SceneOps -> LeRobot)
    """

    INGEST = "ingest"
    EXPORT = "export"
