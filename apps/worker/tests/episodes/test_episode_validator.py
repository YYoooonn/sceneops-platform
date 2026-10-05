"""EpisodeManifestValidator: structural usability of one registered revision.
Asynchronous streams are the canonical form and never an issue."""

from __future__ import annotations

from sceneops_core.episodes.schemas import project_episode_record
from sceneops_core.episodes.testing import (
    action,
    episode_manifest,
    event,
    observation,
    state,
)
from sceneops_worker.episodes.validation import EpisodeManifestValidator


def _record(manifest):
    return project_episode_record(
        dataset_id="d1",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id="episode-manifest-1",
        manifest_checksum=manifest.checksum(),
    )


def test_asynchronous_complete_episode_is_ready() -> None:
    manifest = episode_manifest(
        [observation("/cam", 0), state("/odom", 7, x=1.0), action("/control", 3, u=0.1)]
    )
    result = EpisodeManifestValidator().validate(
        record=_record(manifest), manifest=manifest
    )
    assert result.status == "ready" and not result.issues


def test_record_projection_mismatch_blocks() -> None:
    manifest = episode_manifest(
        [state("/odom", 0, x=1.0), action("/control", 0, u=0.1)]
    )
    record = _record(manifest).model_copy(update={"state_count": 5})
    result = EpisodeManifestValidator().validate(record=record, manifest=manifest)
    assert result.should_block
    assert {i.field for i in result.issues if i.blocking} == {"state_count"}


def test_observation_only_and_marker_only_episodes_warn() -> None:
    manifest = episode_manifest([event("/mission", 0, state="running")])
    result = EpisodeManifestValidator().validate(
        record=_record(manifest), manifest=manifest
    )
    assert result.status == "warning" and not result.should_block
    assert {i.type for i in result.issues} == {
        "no_action_streams",
        "no_stream_occurrences",
    }
