"""Build configuration of the nuScenes Scene builder (ADR-015 §6.5–§6.7;
M3.0 D2, D3).

``NuScenesSceneBuildConfig`` is everything that decides what a registered
nuScenes SourceContent means as canonical Scenes, and nothing else:

    channels   which nuScenes sensor channels (``sensor.channel``, verbatim:
               ``CAM_FRONT``, ``LIDAR_TOP``, ``RADAR_FRONT``, ...) are inside the
               Scene boundary

Everything else is the source's, not configuration: one Scene per native
``scene.token``, its window over every sensor reading the source assigns to
the scene's samples, the clock ``nuscenes.timestamp`` (origin ``unix_epoch``,
1 µs resolution), each channel's modality (``sensor.modality``), each
reading's calibration and ego pose (the reading's own tokens) and the keyframe
groups (one per sample). Which units a build covers (``unit_keys``) is
execution scope, never configuration, so it never enters the fingerprint.
CAN, maps and the annotation tables are preserved in the content and never
read by this builder.

Its normalized form (:meth:`NuScenesSceneBuildConfig.normalized`) is the
producer's ``ProducerInfo.build_config``.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)

from sceneops_core.common.identifiers import validate_verbatim_key

VerbatimKey = Annotated[
    StrictStr, AfterValidator(lambda v: validate_verbatim_key(v, field="channel"))
]


class NuScenesSceneBuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channels: list[VerbatimKey] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_channels(self) -> NuScenesSceneBuildConfig:
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("a channel is selected more than once")
        return self

    def normalized(self) -> dict[str, Any]:
        """``ProducerInfo.build_config``: channels sorted (§27.7)."""
        return {"channels": sorted(self.channels)}


__all__ = ["NuScenesSceneBuildConfig"]
