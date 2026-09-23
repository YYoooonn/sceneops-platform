"""Tests for sceneops_analytics.testing.interop_dataset (SceneOps V2 Request
3.2): the deterministic SceneOps interoperability golden dataset that future
external-format adapters (LeRobot, RLDS, ...) and their round-trip tests
will share.

These tests exercise the bootstrap itself, not any concrete external format
-- SceneOps V2 Request 3.3 later added a concrete, optional LeRobot adapter
(sceneops_analytics.external_adapters.lerobot), but this module and its
bootstrap remain LeRobot/RLDS-free.
"""

from __future__ import annotations

import sys

from sceneops_analytics import SceneOpsDataset
from sceneops_analytics.testing.interop_dataset import (
    EPISODE_A_REV1_REF,
    EPISODE_A_REV2_REF,
    EPISODE_B_REF,
    build_interop_test_dataset,
)
from sceneops_core.episodes.learning_export import learning_data_export_id


async def test_bootstrap_opens_through_sceneops_dataset(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    assert isinstance(bootstrap.dataset, SceneOpsDataset)
    for ref in bootstrap.episode_refs:
        bootstrap.dataset.get_episode(ref)  # does not raise


async def test_exactly_three_episode_refs_exposed(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    assert len(bootstrap.episode_refs) == 3
    assert set(bootstrap.episode_refs) == {
        EPISODE_A_REV1_REF,
        EPISODE_A_REV2_REF,
        EPISODE_B_REF,
    }


async def test_same_episode_id_two_revisions_remain_distinct(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    assert EPISODE_A_REV1_REF.episode_id == EPISODE_A_REV2_REF.episode_id
    assert EPISODE_A_REV1_REF != EPISODE_A_REV2_REF
    assert EPISODE_A_REV1_REF in bootstrap.episode_refs
    assert EPISODE_A_REV2_REF in bootstrap.episode_refs

    rev1 = bootstrap.expected_by_ref[EPISODE_A_REV1_REF]
    rev2 = bootstrap.expected_by_ref[EPISODE_A_REV2_REF]
    assert rev1.steps[0].observation != rev2.steps[0].observation
    assert rev1.steps[0].action != rev2.steps[0].action


async def test_expected_step_counts_and_metadata_match(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    assert bootstrap.expected_by_ref[EPISODE_A_REV1_REF].step_count == 8
    assert bootstrap.expected_by_ref[EPISODE_A_REV2_REF].step_count == 8
    assert bootstrap.expected_by_ref[EPISODE_B_REF].step_count == 6

    for ref, expected in bootstrap.expected_by_ref.items():
        metadata = bootstrap.dataset.get_episode(ref)
        assert metadata.step_count == expected.step_count
        assert metadata.task == expected.task
        assert metadata.outcome == expected.outcome


async def test_timestamps_match_exactly(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    for ref, expected in bootstrap.expected_by_ref.items():
        window = await bootstrap.dataset.get_window(
            ref, 0, expected.step_count, bootstrap.feature_projection
        )
        assert window.timestamps_us == [step.timestamp_us for step in expected.steps]
        # Fixed pattern per Request 3.2 §5.
        assert window.timestamps_us == [i * 100_000 for i in range(expected.step_count)]


async def test_observation_action_values_and_feature_ordering_match(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    for ref, expected in bootstrap.expected_by_ref.items():
        window = await bootstrap.dataset.get_window(
            ref, 0, expected.step_count, bootstrap.feature_projection
        )
        assert window.observation == [step.observation for step in expected.steps]
        assert window.action == [step.action for step in expected.steps]

    schema = await bootstrap.dataset.resolve_feature_schema(
        EPISODE_A_REV1_REF, bootstrap.feature_projection
    )
    assert [entry.channel for entry in schema.observations] == [
        "joint_position",
        "joint_velocity",
        "gripper_position",
    ]
    assert [entry.channel for entry in schema.actions] == [
        "target_joint",
        "gripper_command",
    ]
    assert schema.observation_dim == 7  # 3 + 3 + 1
    assert schema.action_dim == 4  # 3 + 1


async def test_export_id_is_deterministic_semantic_revision_identity(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    expected_export_id = learning_data_export_id(
        aligned_checksums=[
            ref.aligned_artifact_checksum for ref in bootstrap.episode_refs
        ],
        export_config=bootstrap.learning_manifest.export_config,
    )
    assert bootstrap.learning_manifest.export_id == expected_export_id


async def test_two_independent_bootstrap_runs_produce_identical_semantics(
    tmp_path_factory,
):
    bootstrap1 = await build_interop_test_dataset(tmp_path_factory.mktemp("run1"))
    bootstrap2 = await build_interop_test_dataset(tmp_path_factory.mktemp("run2"))

    assert bootstrap1.episode_refs == bootstrap2.episode_refs
    assert bootstrap1.expected_episodes == bootstrap2.expected_episodes
    assert (
        bootstrap1.learning_manifest.export_id == bootstrap2.learning_manifest.export_id
    )
    assert (
        bootstrap1.learning_manifest.dataset_id
        == bootstrap2.learning_manifest.dataset_id
    )
    assert (
        bootstrap1.learning_manifest.dataset_version
        == bootstrap2.learning_manifest.dataset_version
    )

    # Filesystem-derived fields legitimately differ across runs -- proving
    # this isn't a vacuous "everything happens to be equal" comparison.
    assert (
        bootstrap1.learning_manifest.table_uris
        != bootstrap2.learning_manifest.table_uris
    )


async def test_no_lerobot_or_rlds_dependency_is_required(tmp_path):
    # A snapshot-before/after-delta check, not a bare absence check: SceneOps
    # V2 Request 3.3 added a real, optional LeRobot adapter elsewhere in this
    # package (sceneops_analytics.external_adapters.lerobot), so lerobot may
    # already be loaded in this process by the time this test runs (e.g. an
    # earlier test module imported it) -- that is expected and fine. What
    # this test actually guards is narrower and still holds: building the
    # interop golden dataset itself never imports lerobot/rlds as a side
    # effect.
    before = {
        name
        for name in sys.modules
        if "lerobot" in name.lower() or "rlds" in name.lower()
    }

    await build_interop_test_dataset(tmp_path)

    after = {
        name
        for name in sys.modules
        if "lerobot" in name.lower() or "rlds" in name.lower()
    }
    assert after == before
