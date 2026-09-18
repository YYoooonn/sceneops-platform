from __future__ import annotations

from sceneops_core.episodes.schemas import EpisodeManifest

from .reports import EpisodeProfileResult


class EpisodeManifestProfiler:
    """Pure descriptive stats for one Episode manifest (SceneOps V2 Request 17).

    Answers "what kind of trajectory/data does this Episode contain?" — no
    quality judgment here, that's EpisodeManifestValidator's job. All values
    come directly from fields EpisodeBuilder already computed; nothing here
    re-derives or re-aligns timestamps.
    """

    def profile(self, *, manifest: EpisodeManifest) -> EpisodeProfileResult:
        duration_us = None
        if (
            manifest.start_timestamp_us is not None
            and manifest.end_timestamp_us is not None
        ):
            duration_us = manifest.end_timestamp_us - manifest.start_timestamp_us

        return EpisodeProfileResult(
            episode_id=manifest.episode_id,
            frame_count=manifest.frame_count,
            observation_count=len(manifest.observation_frames),
            action_count=len(manifest.action_frames),
            observation_channels=list(manifest.observation_channels),
            action_channels=list(manifest.action_channels),
            control_frequency_hz=manifest.control_frequency_hz,
            start_timestamp_us=manifest.start_timestamp_us,
            end_timestamp_us=manifest.end_timestamp_us,
            duration_us=duration_us,
            task=manifest.task,
            outcome=str(manifest.outcome),
            mission_id=manifest.lineage.mission_id,
        )
