from __future__ import annotations

from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import EpisodeManifest

from .reports import EpisodeProfileResult, EpisodeStreamProfile


class EpisodeManifestProfiler:
    """Pure descriptive statistics of one Episode revision: per role and per
    stream counts and extents, each stream in its own clock. No quality
    judgment (validation's job) and no cross-stream timing (alignment's)."""

    def profile(
        self, *, episode_id: str, manifest: EpisodeManifest
    ) -> EpisodeProfileResult:
        streams: list[EpisodeStreamProfile] = []
        for stream in manifest.streams:
            stamps = [
                o.timestamp_ns
                for o in manifest.occurrences()
                if o.topic == stream.topic
            ]
            streams.append(
                EpisodeStreamProfile(
                    topic=stream.topic,
                    role=stream.role.value,
                    source_clock=stream.source_clock,
                    count=len(stamps),
                    first_timestamp_ns=min(stamps) if stamps else None,
                    last_timestamp_ns=max(stamps) if stamps else None,
                    duplicate_timestamp_count=len(stamps) - len(set(stamps)),
                )
            )
        window = manifest.declared_window()
        return EpisodeProfileResult(
            episode_id=episode_id,
            observation_count=len(manifest.observations),
            state_count=len(manifest.states),
            action_count=len(manifest.actions),
            event_count=len(manifest.events),
            observation_topics=manifest.observed_topics(EpisodeStreamRole.OBSERVATION),
            state_topics=manifest.observed_topics(EpisodeStreamRole.STATE),
            action_topics=manifest.observed_topics(EpisodeStreamRole.ACTION),
            event_topics=manifest.observed_topics(EpisodeStreamRole.EVENT),
            window_clock=window.source_clock,
            window_duration_ns=window.end_timestamp_ns - window.start_timestamp_ns,
            streams=streams,
        )
