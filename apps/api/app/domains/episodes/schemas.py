from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.schemas.records import EpisodeRecord


class EpisodeDetailResponse(SceneOpsBaseModel):
    episode: EpisodeRecord


class EpisodeListResponse(SceneOpsBaseModel):
    episodes: list[EpisodeRecord]
    count: int
