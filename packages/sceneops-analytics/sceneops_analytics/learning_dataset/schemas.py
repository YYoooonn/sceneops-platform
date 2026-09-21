from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.learning import EpisodeRef
from sceneops_core.episodes.schemas.enums import EpisodeOutcome


class EpisodeMetadata(SceneOpsBaseModel):
    """Episode-level fields from one learning_episodes.parquet row (SceneOps
    V2 Request 2.7B §4) -- everything SceneOpsDataset.get_episode() can
    answer without touching learning_steps/learning_signals at all."""

    episode_ref: EpisodeRef

    source_start_timestamp_us: int
    source_end_timestamp_us: int
    source_clock: str

    alignment_semantics_version: str
    alignment_config_hash: str

    target_frequency_hz: float
    achieved_frequency_hz: float
    dt_us: int

    step_count: int
    duplicate_discarded_count: int

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN
