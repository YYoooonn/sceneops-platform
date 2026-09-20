from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.schemas import EpisodeSegmentationConfig

from .base import BaseJobParams


class BuildEpisodesJobParams(BaseJobParams):
    """Robot rosbag/MCAP -> episodes.

    Mirrors ``BuildScenesJobParams`` (raw log -> scenes), but the source
    adapter is always ``RosbagAdapter`` (registered under
    ``RawLogSourceType.REAL_ROBOT_LOG`` in this job's own adapter factory,
    separate from ``build_scenes``'s) and segmentation is driven by
    ``EpisodeSegmenter`` (see ``segmentation`` below) rather than a generic
    gap/anchor scene segmenter.
    """

    dataset_id: str
    dataset_version: str

    robot_id: str
    robot_run_id: str | None = None

    # Falls back to the referenced RobotRun's mcap_uri/rosbag_uri when
    # omitted — same convention as IngestRobotStatesJobParams.
    mcap_uri: str | None = None

    raw_log_id: str | None = None

    # Default (mission_boundary, falling back to whole_run when no dated
    # Mission exists) preserves pre-Request-13 EpisodeBuilder behavior
    # exactly — see SceneOps V2 Request 13.
    segmentation: EpisodeSegmentationConfig = Field(
        default_factory=EpisodeSegmentationConfig
    )

    max_built_episodes: int | None = None

    output_episode_root_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RegisterEpisodeJobParams(BaseJobParams):
    episode_ids: list[str] = Field(default_factory=list)
    episode_manifest_uris: list[str] = Field(default_factory=list)

    dataset_id: str | None = None
    dataset_version: str | None = None

    replace_existing: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class ValidateEpisodeJobParams(BaseJobParams):
    """EpisodeRecord + EpisodeManifest -> structural usability check.

    Keyed by episode_id (not manifest URI) — unlike build_episodes/
    register_episode, this runs after register_episode, so EpisodeRecord
    already exists and carries its own episode_manifest_uri; there's no need
    to thread manifest URIs through separately (see SceneOps V2 Request 17).
    """

    episode_id: str | None = None
    episode_ids: list[str] = Field(default_factory=list)

    dataset_id: str | None = None
    dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ProfileEpisodeJobParams(BaseJobParams):
    episode_id: str | None = None
    episode_ids: list[str] = Field(default_factory=list)

    dataset_id: str | None = None
    dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
