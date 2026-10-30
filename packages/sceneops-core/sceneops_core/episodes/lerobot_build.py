"""Build configuration of the LeRobot Episode builder (ADR-015 §6.6, §6.7;
M3.0 D2, D8, D9, D13).

``LeRobotEpisodeBuildConfig`` is everything that decides what a registered
LeRobot SourceContent means as canonical Episodes, and nothing else:

    features      which per-frame dataset features are inside the Episode, each
                  with its canonical role: ``observation`` (a video feature
                  becomes a preserved-file payload, a numeric one a value),
                  ``state``, ``action`` or ``transition`` (a per-frame signal
                  about the step to the next frame, e.g. ``next.reward``)
    task_events   whether the per-frame task is kept as ``task_index`` events:
                  one at every frame whose task differs from the previous frame's

The unit boundary, the clock and the time rule are the source's, not
configuration: one Episode per native ``episode_index``, on
``lerobot.frame_time`` with origin ``unit``, frame ``k`` at
``round_half_even(k · 10⁹ / fps)``. The structural features that carry them
(``timestamp``, ``frame_index``, ``episode_index``, ``index``, ``task_index``)
cannot be selected as values. Which units a build covers (``unit_keys``) is
execution scope, never configuration, so it never enters the fingerprint.

Its normalized form (:meth:`LeRobotEpisodeBuildConfig.normalized`) is the
producer's ``ProducerInfo.build_config``.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from .recording_build import EpisodeStreamRole, FieldPath

# The features that carry the frame grid, the unit, the native occurrence
# reference and the task: represented by time, unit key, native_ref and task
# events, never selected as values.
LEROBOT_STRUCTURAL_FEATURES: Final = frozenset(
    {"timestamp", "frame_index", "episode_index", "index", "task_index"}
)


def _selectable_feature(value: str) -> str:
    if value in LEROBOT_STRUCTURAL_FEATURES:
        raise ValueError(
            f"{value!r} is structural (time, unit, native_ref or task) and cannot "
            "be selected as a feature"
        )
    return value


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LeRobotFeatureSelection(_ConfigModel):
    # A LeRobot feature key, verbatim ("observation.state", "next.reward"); it
    # must also be a field path, since it names the column a value is read from.
    feature: Annotated[FieldPath, AfterValidator(_selectable_feature)]
    role: Literal[
        EpisodeStreamRole.OBSERVATION,
        EpisodeStreamRole.STATE,
        EpisodeStreamRole.ACTION,
        EpisodeStreamRole.TRANSITION,
    ]


class LeRobotEpisodeBuildConfig(_ConfigModel):
    features: list[LeRobotFeatureSelection] = Field(min_length=1)
    task_events: StrictBool = True

    @model_validator(mode="after")
    def _unique_features(self) -> LeRobotEpisodeBuildConfig:
        names = [f.feature for f in self.features]
        if len(names) != len(set(names)):
            raise ValueError("a feature is selected more than once")
        return self

    def normalized(self) -> dict[str, Any]:
        """``ProducerInfo.build_config``: features sorted (§27.7)."""
        data = self.model_dump(mode="json")
        data["features"] = sorted(data["features"], key=lambda f: f["feature"])
        return data


__all__ = [
    "LEROBOT_STRUCTURAL_FEATURES",
    "LeRobotEpisodeBuildConfig",
    "LeRobotFeatureSelection",
]
