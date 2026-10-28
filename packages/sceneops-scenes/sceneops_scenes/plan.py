"""The output of a Scene builder (M3.0 D7).

Every Scene builder -- one per source format -- returns a ``SceneBuildPlan``:
the complete SceneManifest set of its scope and the payloads the build must
publish. Manifest publication and registration are shared by every builder;
a builder that references preserved source files plans no payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.provenance import ProducerInfo
from sceneops_core.scenes.schemas import SceneManifest
from sceneops_recording.observations.payloads import PlannedPayload


@dataclass(frozen=True)
class PlannedScene:
    unit_key: str
    manifest: SceneManifest


@dataclass(frozen=True)
class SceneBuildPlan:
    producer: ProducerInfo
    scenes: list[PlannedScene]
    # keyed by (topic, per-channel file index)
    payloads: dict[tuple[str, int], PlannedPayload]
    pose_count: int

    @property
    def observation_count(self) -> int:
        return sum(len(s.manifest.observations) for s in self.scenes)

    @property
    def keyframe_count(self) -> int:
        return sum(len(s.manifest.keyframes()) for s in self.scenes)


__all__ = ["PlannedScene", "SceneBuildPlan"]
