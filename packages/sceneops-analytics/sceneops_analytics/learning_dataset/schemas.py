from __future__ import annotations

from typing import Any

from pydantic import StrictStr, field_validator, model_validator

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.learning import EpisodeRef
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_core.temporal import (
    ClockInstance,
    ClockOrigin,
    restore_clock_instance,
)


class EpisodeMetadata(SceneOpsBaseModel):
    """Episode-level fields from one learning_episodes.parquet row (SceneOps
    V2 Request 2.7B §4) -- everything SceneOpsDataset.get_episode() can
    answer without touching learning_steps/learning_signals at all.

    ``clock_instance`` is the origin instance every timestamp of this Episode,
    its steps and its signals is on: two Episodes' timestamps are comparable
    exactly when their instances are equal (``sceneops_core.temporal``). A
    consumer compares the whole value and never reads what a scope element
    names. The shape is checked; that the scope belongs to this Episode is not,
    which takes the Episode's canonical manifest."""

    episode_ref: EpisodeRef

    source_start_timestamp_us: int
    source_end_timestamp_us: int
    source_clock: str
    clock_origin: ClockOrigin
    clock_scope: tuple[StrictStr, ...]

    alignment_semantics_version: str
    alignment_config_hash: str

    target_frequency_hz: float
    achieved_frequency_hz: float
    dt_us: int

    step_count: int
    duplicate_discarded_count: int

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN

    @field_validator("clock_scope", mode="before")
    @classmethod
    def _scope_is_ordered(cls, value: Any) -> Any:
        # Pydantic would otherwise build the tuple from any iterable, and the
        # iteration order of a set is not a scope.
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"clock_scope must be an ordered sequence, got {type(value).__name__}"
            )
        return value

    @model_validator(mode="after")
    def _clock_context_is_one_instance(self) -> EpisodeMetadata:
        restore_clock_instance(self.source_clock, self.clock_origin, self.clock_scope)
        return self

    @property
    def clock_instance(self) -> ClockInstance:
        return restore_clock_instance(
            self.source_clock, self.clock_origin, self.clock_scope
        )
