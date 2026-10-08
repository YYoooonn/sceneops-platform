"""EpisodeManifestProfiler: per-role and per-stream description, each stream
in its own clock; duplicates are counted, never collapsed."""

from __future__ import annotations

from sceneops_core.episodes.testing import action, episode_manifest, state
from sceneops_episodes.profiling import EpisodeManifestProfiler


def test_profile_describes_streams_without_aligning_them() -> None:
    manifest = episode_manifest(
        [
            state("/odom", 1, x=1.0),
            state("/odom", 1, x=1.0),
            state("/odom", 4, x=2.0),
            action("/control", 2, clock="robot.control", u=0.1),
        ],
        window=(0, 10),
    )
    profile = EpisodeManifestProfiler().profile(episode_id="ep-1", manifest=manifest)
    assert (profile.state_count, profile.action_count) == (3, 1)
    assert profile.window_duration_ns == 10
    streams = {s.topic: s for s in profile.streams}
    assert streams["/odom"].duplicate_timestamp_count == 1
    assert (
        streams["/odom"].first_timestamp_ns,
        streams["/odom"].last_timestamp_ns,
    ) == (1, 4)
    assert streams["/control"].source_clock == "robot.control"
