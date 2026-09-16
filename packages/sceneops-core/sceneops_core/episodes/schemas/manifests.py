from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import SensorModality

from .enums import EpisodeOutcome


class EpisodeLineage(SceneOpsBaseModel):
    raw_log_id: str | None = None

    robot_id: str | None = None
    robot_run_id: str | None = None
    mission_id: str | None = None

    source_dataset_id: str | None = None
    source_dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class EpisodeObservationFrame(SceneOpsBaseModel):
    """One observation sample at a point in time.

    Two shapes share this model: camera/lidar sensor frames (``modality`` set,
    ``uri`` points at the stored frame) and robot-state-derived observations
    such as position/orientation/velocity/battery (``modality`` is None,
    the sample value lives in ``values``). Keeping them in one list — rather
    than two separate ones — matches how a LeRobot-style export ultimately
    needs both pivoted onto the same per-timestep row.
    """

    timestamp_us: int
    channel: str

    modality: SensorModality | None = None
    uri: str | None = None

    values: list[float] | None = None

    metadata: JsonDict = Field(default_factory=dict)


class EpisodeActionFrame(SceneOpsBaseModel):
    timestamp_us: int
    channel: str
    value: float

    metadata: JsonDict = Field(default_factory=dict)


class EpisodeManifest(SceneOpsBaseModel):
    """Full episode representation (frame-level payload, stored in ArtifactStore).

    ``EpisodeRecord`` is the Postgres-facing summary of this manifest, mirroring
    how ``SceneRecord`` summarizes ``SceneManifest``.
    """

    episode_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    lineage: EpisodeLineage = Field(default_factory=EpisodeLineage)

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN

    observation_frames: list[EpisodeObservationFrame] = Field(default_factory=list)
    action_frames: list[EpisodeActionFrame] = Field(default_factory=list)

    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)
    control_frequency_hz: float | None = None

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None

    frame_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)
