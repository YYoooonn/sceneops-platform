"""Correctness smoke test for the SceneOps V2 Request 5.1 scaling benchmark
fixture (sceneops_analytics.testing.scale_fixture) -- fast (xs scale only),
part of the regular unit-test tier. The benchmark harness itself
(scripts/dev/benchmark_learning_data_scaling.py) is not run by pytest.
"""

from __future__ import annotations

import pytest

from sceneops_analytics.learning_dataset import SceneOpsDataset
from sceneops_analytics.testing import (
    ScaleSpec,
    all_episode_keys,
    build_scaled_entries,
    episode_ref,
    episode_revisions,
    expected_action,
    expected_observation,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.alignment import AlignedSignalStatus
from sceneops_core.episodes.learning_export import ShardPolicy
from sceneops_storage import LocalArtifactStore

XS_SPEC = ScaleSpec(name="xs-test", num_episodes=3, steps_per_episode=5)

# A tiny spec with every realistic-variation knob turned on (SceneOps V2
# Request 5.2 §1), used only by the variation-specific tests below --
# XS_SPEC above deliberately stays legacy/uniform so the Request 5.1 tests
# it backs keep testing the "everything off" default path.
VARIED_SPEC = ScaleSpec(
    name="varied-test",
    num_episodes=6,
    steps_per_episode=10,
    num_observation_channels=4,
    num_action_channels=3,
    num_core_observation_channels=2,
    num_core_action_channels=1,
    length_jitter_fraction=0.4,
    extra_revision_every=3,
    extra_channel_absent_period=3,
    extra_channel_missing_period=4,
)


async def _open_dataset(
    tmp_path, spec: ScaleSpec
) -> tuple[SceneOpsDataset, LocalArtifactStore]:
    artifacts = await write_scaled_dataset_artifacts(tmp_path, spec)
    artifact_store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    dataset = await SceneOpsDataset.open(
        learning_manifest=artifacts.learning_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=artifact_store,
    )
    return dataset, artifact_store, artifacts


async def test_scaled_fixture_exposes_every_episode_ref(tmp_path):
    dataset, _store, _artifacts = await _open_dataset(tmp_path, XS_SPEC)

    refs = dataset.episodes()
    assert len(refs) == XS_SPEC.num_episodes
    assert set(refs) == {episode_ref(XS_SPEC, i) for i in range(XS_SPEC.num_episodes)}
    for ref in refs:
        assert dataset.get_episode(ref).step_count == XS_SPEC.steps_per_episode


async def test_scaled_fixture_window_matches_independent_formula(tmp_path):
    dataset, _store, artifacts = await _open_dataset(tmp_path, XS_SPEC)

    ref = episode_ref(XS_SPEC, 1)
    window = await dataset.get_window(
        ref, 0, XS_SPEC.steps_per_episode, artifacts.feature_projection
    )

    for step_index in range(XS_SPEC.steps_per_episode):
        assert window.observation[step_index] == pytest.approx(
            expected_observation(XS_SPEC, 1, step_index)
        )
        assert window.action[step_index] == pytest.approx(
            expected_action(XS_SPEC, 1, step_index)
        )


async def test_scaled_fixture_single_step_matches_full_window(tmp_path):
    dataset, _store, artifacts = await _open_dataset(tmp_path, XS_SPEC)

    ref = episode_ref(XS_SPEC, 0)
    step_sample = await dataset.project_step(ref, 2, artifacts.feature_projection)

    assert step_sample.observation == pytest.approx(expected_observation(XS_SPEC, 0, 2))
    assert step_sample.action == pytest.approx(expected_action(XS_SPEC, 0, 2))


# ----------------------------------------------------------------------
# Realistic-variation coverage (SceneOps V2 Request 5.2 §1) -- these only
# exercise VARIED_SPEC, never XS_SPEC, so the Request 5.1 tests above keep
# testing the "everything off" default path unchanged.
# ----------------------------------------------------------------------


def test_length_jitter_varies_step_count_across_episodes():
    entries = build_scaled_entries(VARIED_SPEC)
    step_counts = {
        artifact.aligned_episode.step_count for _checksum, artifact in entries
    }
    assert len(step_counts) > 1
    for count in step_counts:
        assert count > 0


def test_extra_revision_every_produces_multiple_refs_for_some_episode_ids():
    keys = all_episode_keys(VARIED_SPEC)
    assert len(keys) > VARIED_SPEC.num_episodes  # some episodes got a 2nd revision

    revision_counts = {
        episode_index: len(episode_revisions(VARIED_SPEC, episode_index))
        for episode_index in range(VARIED_SPEC.num_episodes)
    }
    assert set(revision_counts.values()) == {1, 2}

    entries = build_scaled_entries(VARIED_SPEC)
    by_episode_id: dict[str, set[str]] = {}
    for checksum, artifact in entries:
        by_episode_id.setdefault(artifact.aligned_episode.episode_id, set()).add(
            checksum
        )
    multi_revision_ids = [
        eid for eid, checksums in by_episode_id.items() if len(checksums) > 1
    ]
    assert multi_revision_ids  # at least one episode_id has >1 distinct checksum


def test_task_and_outcome_cycle_across_episodes():
    entries = build_scaled_entries(VARIED_SPEC)
    tasks = {artifact.aligned_episode.task for _checksum, artifact in entries}
    outcomes = {artifact.aligned_episode.outcome for _checksum, artifact in entries}
    assert len(tasks) > 1
    assert len(outcomes) > 1


def test_extra_channel_goes_absent_and_missing_but_core_never_does():
    entries = build_scaled_entries(VARIED_SPEC)
    core_obs = set(VARIED_SPEC.core_observation_channels)
    core_act = set(VARIED_SPEC.core_action_channels)
    extra_obs = set(VARIED_SPEC.observation_channels) - core_obs
    extra_act = set(VARIED_SPEC.action_channels) - core_act
    assert extra_obs and extra_act  # spec actually has extra channels to check

    saw_absent_extra = False
    saw_missing_extra = False
    for _checksum, artifact in entries:
        for step in artifact.aligned_episode.steps:
            for channel in core_obs | core_act:
                signal = step.observations.get(channel) or step.actions.get(channel)
                assert (
                    signal is not None
                ), f"core channel {channel} must never be ABSENT"
                assert (
                    signal.status == AlignedSignalStatus.RESOLVED
                ), f"core channel {channel} must never be MISSING"
            for channel in extra_obs | extra_act:
                signal = step.observations.get(channel) or step.actions.get(channel)
                if signal is None:
                    saw_absent_extra = True
                elif signal.status == AlignedSignalStatus.MISSING:
                    saw_missing_extra = True

    assert saw_absent_extra
    assert saw_missing_extra


async def test_varied_spec_still_projects_core_channels_end_to_end(tmp_path):
    dataset, _store, artifacts = await _open_dataset(tmp_path, VARIED_SPEC)

    for episode_index in range(VARIED_SPEC.num_episodes):
        for revision in episode_revisions(VARIED_SPEC, episode_index):
            ref = episode_ref(VARIED_SPEC, episode_index, revision)
            metadata = dataset.get_episode(ref)
            window = await dataset.get_window(
                ref, 0, metadata.step_count, artifacts.feature_projection
            )
            for step_index in range(metadata.step_count):
                assert window.observation[step_index] == pytest.approx(
                    expected_observation(
                        VARIED_SPEC, episode_index, step_index, revision
                    )
                )
                assert window.action[step_index] == pytest.approx(
                    expected_action(VARIED_SPEC, episode_index, step_index, revision)
                )


async def test_shard_policy_splits_episodes_across_multiple_shards(tmp_path):
    tight_policy = ShardPolicy(max_episodes_per_shard=2, max_rows_per_shard=10_000)
    artifacts = await write_scaled_dataset_artifacts(
        tmp_path, XS_SPEC, shard_policy=tight_policy
    )

    shard_index = artifacts.learning_manifest.shard_index
    assert shard_index is not None
    # 3 episodes, max 2 per shard -> 2 shards
    assert len(shard_index.learning_steps) == 2
    assert len(shard_index.learning_signals) == 2
    for shard in shard_index.learning_steps:
        assert len(shard.episodes) <= 2

    artifact_store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    dataset = await SceneOpsDataset.open(
        learning_manifest=artifacts.learning_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=artifact_store,
    )
    assert len(dataset.episodes()) == XS_SPEC.num_episodes
