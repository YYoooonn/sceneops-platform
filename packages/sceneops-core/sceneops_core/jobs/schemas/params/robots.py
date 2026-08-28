from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobParams


class IngestRobotStatesJobParams(BaseJobParams):
    """Read robot runtime state topics from a rosbag2/MCAP file into RobotState rows.

    Not tied to a Dataset/DatasetVersion — RobotRun is a separate domain from
    SceneOps' dataset ingestion pipelines (docs/robot-data-model.md §5).
    """

    robot_id: str
    robot_run_id: str | None = None

    # Falls back to the referenced RobotRun's mcap_uri/rosbag_uri when omitted.
    mcap_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
