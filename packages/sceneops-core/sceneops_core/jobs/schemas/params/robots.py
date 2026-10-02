from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobParams, RecordingConsumerJobParams


class RegisterRobotRunJobParams(BaseJobParams):
    """REGISTER_ROBOT_RUN: verify a published RobotRunManifest and the
    recording it references, then project them into ArtifactRecords + one
    RobotRunRecord (ADR-007 §12.1). Not tied to a Dataset/DatasetVersion."""

    manifest_uri: str = Field(min_length=1)


class IngestRobotStatesJobParams(RecordingConsumerJobParams):
    """Read robot runtime state topics from a registered RobotRun recording
    into RobotState rows.

    Not tied to a Dataset/DatasetVersion — RobotRun is a separate domain from
    SceneOps' dataset ingestion pipelines (docs/architecture/data-model.md §5).

    The recording -- and the robot it belongs to -- is identified only by
    ``robot_run_id`` (see ``RecordingConsumerJobParams``).
    """

    metadata: JsonDict = Field(default_factory=dict)


class ExportRobotAnalyticsSnapshotJobParams(BaseJobParams):
    """Export RobotState/Mission rows for one RobotRun to Parquet.

    Mirrors ExportAnalyticsSnapshotJobParams (dataset-scoped) but scoped by
    robot_run_id instead — Robot/RobotRun is a separate domain from
    Dataset/DatasetVersion (docs/architecture/data-model.md §5).
    """

    robot_run_id: str

    # None → export all known tables (robot_telemetry, missions)
    tables: list[str] | None = None

    metadata: JsonDict = Field(default_factory=dict)
