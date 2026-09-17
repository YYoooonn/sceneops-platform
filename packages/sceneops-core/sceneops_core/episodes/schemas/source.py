from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.robots.schemas import MissionRecord, RobotStateRecord


class EpisodeSource(SceneOpsBaseModel):
    """RosbagAdapter's Episode-domain read of one MCAP/rosbag2 recording.

    This is the whole of what EpisodeBuilder ever needed from a raw log —
    camera/lidar sensor frames (for observation channels), robot state
    samples (position/velocity/... -> observations, steering/throttle/brake
    -> actions), and mission boundaries (segmentation signal). It deliberately
    excludes ``RawLogManifest``/``RawLogFrameIndex`` — those are Scene-owned
    artifacts (channels/modalities summary, calibrations, ego_poses,
    persisted manifest) that EpisodeBuilder never read even when
    ``build_episodes`` was producing them as a side effect of getting this
    frame list. See SceneOps V2 Request 12.
    """

    frames: list[RawSensorFrameManifest] = Field(default_factory=list)
    robot_states: list[RobotStateRecord] = Field(default_factory=list)
    missions: list[MissionRecord] = Field(default_factory=list)
