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
    episode_ref,
    expected_action,
    expected_observation,
    write_scaled_dataset_artifacts,
)
from sceneops_storage import LocalArtifactStore

XS_SPEC = ScaleSpec(name="xs-test", num_episodes=3, steps_per_episode=5)


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
