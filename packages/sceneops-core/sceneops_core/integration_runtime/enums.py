from __future__ import annotations

from enum import StrEnum


class IntegrationOperation(StrEnum):
    """Direction of one interoperability-runtime execution.

    EXPORT   SceneOps canonical -> ExternalDatasetRef (e.g. SceneOps -> LeRobot)

    There is no ingest direction: acquired data enters SceneOps only as a
    registered RobotRun recording (ADR-007 §29.2, I-31). An external
    dataset reaches SceneOps through the acquisition tool, never through an
    integration runtime.
    """

    EXPORT = "export"
