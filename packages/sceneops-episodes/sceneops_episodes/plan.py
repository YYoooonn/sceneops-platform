"""The output of an Episode builder (M3.0 D7).

Every Episode builder -- one per source format -- returns an
``EpisodeBuildPlan``: the complete EpisodeManifest set of its scope and the
payloads the build must publish. Manifest publication and registration are
shared by every builder; a builder that references preserved source files
plans no payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_core.provenance import ProducerInfo
from sceneops_recording.observations.payloads import PlannedPayload


@dataclass(frozen=True)
class PlannedEpisode:
    unit_key: str
    manifest: EpisodeManifest


@dataclass(frozen=True)
class EpisodeBuildPlan:
    producer: ProducerInfo
    episodes: list[PlannedEpisode]
    # keyed by (topic, per-topic file index)
    payloads: dict[tuple[str, int], PlannedPayload]

    def count(self, role: EpisodeStreamRole) -> int:
        return sum(len(e.manifest.occurrences_of(role)) for e in self.episodes)


__all__ = ["EpisodeBuildPlan", "PlannedEpisode"]
