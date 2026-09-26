"""Selective-read correctness (SceneOps V2 Request 5.3): v2-sharded
EpisodeRef/window access must be selective (no unrelated shard touched),
must detect malformed shard mappings clearly, and must produce results
identical to the legacy v1 whole-table reader over the same content.

Uses ``CountingArtifactStore`` throughout to prove selectivity from actual
I/O behavior, not just output equality.
"""

from __future__ import annotations

import pytest

from sceneops_analytics import (
    AnalyticsTableWriter,
    SceneOpsDataset,
    ShardIndexMismatchError,
)
from sceneops_analytics.learning_tables import (
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from sceneops_analytics.testing import (
    CountingArtifactStore,
    ScaleSpec,
    build_scaled_entries,
    episode_ref,
    episode_revisions,
    expected_observation,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.alignment import AlignedSignalStatus
from sceneops_core.episodes.learning_export import (
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    ShardPolicy,
    learning_data_export_id,
)
from sceneops_storage import LocalArtifactStore

# Small enough that a tight ShardPolicy produces multiple distinct shards
# -- the whole point of this module's tests is proving "one shard touched,
# not others."
MULTI_SHARD_SPEC = ScaleSpec(
    name="multi-shard-test", num_episodes=6, steps_per_episode=8
)
TIGHT_POLICY = ShardPolicy(max_episodes_per_shard=2, max_rows_per_shard=10_000)

VARIED_SPEC = ScaleSpec(
    name="varied-selective-test",
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


async def _open_counted(
    tmp_path, spec: ScaleSpec, *, shard_policy: ShardPolicy | None = None
):
    artifacts = await write_scaled_dataset_artifacts(
        tmp_path, spec, shard_policy=shard_policy
    )
    store = CountingArtifactStore(
        LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    )
    dataset = await SceneOpsDataset.open(
        learning_manifest=artifacts.learning_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=store,
    )
    return dataset, store, artifacts


async def _open_legacy(tmp_path, spec: ScaleSpec):
    """Build the SAME entries under the legacy v1 single-file layout, for
    direct v1-vs-v2 result-equivalence comparisons."""
    dataset_id = f"legacy-{spec.name}"
    dataset_version = "v1"
    storage_root_uri = str(tmp_path / "storage")
    artifact_store = LocalArtifactStore(root_uri=storage_root_uri)
    entries = build_scaled_entries(spec)

    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[c for c, _ in entries], export_config=export_config
    )
    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=str(tmp_path / "analytics")
    )
    builders = {
        "learning_episodes": build_learning_episodes_table,
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    table_uris, table_checksums, row_counts = {}, {}, {}
    for name, builder in builders.items():
        df = builder(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            name,
            df,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
        )
        table_uris[name] = result.uri
        table_checksums[name] = result.checksum
        row_counts[name] = df.height

    manifest = LearningDataExportManifest(
        export_id=export_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        inputs=[
            AlignedArtifactRevision(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_id=f"art-{c}",
                aligned_artifact_checksum=c,
            )
            for c, artifact in entries
        ],
        export_config=export_config,
        table_uris=table_uris,
        table_checksums=table_checksums,
        row_counts=row_counts,
        episode_count=spec.num_episodes,
    )
    write_result = await writer.write_learning_export_manifest(
        manifest,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
    )
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=write_result.checksum,
        artifact_store=artifact_store,
    )
    return dataset


# ----------------------------------------------------------------------
# Selectivity: no unrelated shard touched
# ----------------------------------------------------------------------


async def test_v2_single_episode_access_touches_only_its_own_shard(tmp_path):
    dataset, store, artifacts = await _open_counted(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    assert dataset._is_sharded is True

    shard_index = artifacts.learning_manifest.shard_index
    assert len(shard_index.learning_steps) >= 3  # 6 episodes / 2 per shard

    refs = dataset.episodes()
    target_ref = refs[0]
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    await dataset.get_window(
        target_ref, 0, dataset.get_episode(target_ref).step_count, projection
    )

    touched_uris = set(store.stats.per_uri_range_calls) | set(
        store.stats.per_uri_read_calls
    )
    # Only the target's own steps shard + signals shard (never any other
    # shard file) plus learning_episodes.parquet (whole-object, at open()).
    other_shard_uris = {
        shard.uri
        for table_name in ("learning_steps", "learning_signals")
        for shard in getattr(shard_index, table_name)
        for member in shard.episodes
        if member.episode_ref != target_ref
    } - {
        shard.uri
        for table_name in ("learning_steps", "learning_signals")
        for shard in getattr(shard_index, table_name)
        for member in shard.episodes
        if member.episode_ref == target_ref
    }
    assert touched_uris.isdisjoint(other_shard_uris)


async def test_v2_warm_second_episode_same_shard_skips_footer_refetch(tmp_path):
    dataset, store, artifacts = await _open_counted(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    shard_index = artifacts.learning_manifest.shard_index
    first_shard = shard_index.learning_steps[0]
    assert len(first_shard.episodes) == 2  # tight policy: 2 episodes/shard

    ref_a = first_shard.episodes[0].episode_ref
    ref_b = first_shard.episodes[1].episode_ref
    projection = feature_projection_for(MULTI_SHARD_SPEC)

    await dataset.get_window(
        ref_a, 0, dataset.get_episode(ref_a).step_count, projection
    )
    calls_after_first = store.stats.read_range_calls
    await dataset.get_window(
        ref_b, 0, dataset.get_episode(ref_b).step_count, projection
    )
    calls_for_second = store.stats.read_range_calls - calls_after_first

    # Second episode in the SAME shard: only 2 more range reads expected
    # (one row-group fetch per table), not another footer fetch pair.
    assert calls_for_second == 2


# ----------------------------------------------------------------------
# Duplicate / missing shard mapping detection
# ----------------------------------------------------------------------


async def test_duplicate_episode_ref_in_shard_index_raises_at_open(tmp_path):
    artifacts = await write_scaled_dataset_artifacts(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    manifest = artifacts.learning_manifest
    shard_index = manifest.shard_index
    # Duplicate the first shard's first member into the second shard too.
    dup_member = shard_index.learning_steps[0].episodes[0]
    mutated_second_shard = shard_index.learning_steps[1].model_copy(
        update={"episodes": [*shard_index.learning_steps[1].episodes, dup_member]}
    )
    mutated_shard_index = shard_index.model_copy(
        update={
            "learning_steps": [
                shard_index.learning_steps[0],
                mutated_second_shard,
                *shard_index.learning_steps[2:],
            ]
        }
    )
    mutated_manifest = manifest.model_copy(update={"shard_index": mutated_shard_index})

    store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    with pytest.raises(ShardIndexMismatchError):
        await SceneOpsDataset.open(
            learning_manifest=mutated_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store,
        )


async def test_missing_episode_ref_in_shard_index_raises_on_access(tmp_path):
    artifacts = await write_scaled_dataset_artifacts(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    manifest = artifacts.learning_manifest
    shard_index = manifest.shard_index

    # Drop the first shard's first episode member entirely from
    # learning_steps -- learning_episodes.parquet still declares it, but
    # no shard maps it now.
    dropped_ref = shard_index.learning_steps[0].episodes[0].episode_ref
    mutated_first_shard = shard_index.learning_steps[0].model_copy(
        update={"episodes": shard_index.learning_steps[0].episodes[1:]}
    )
    mutated_shard_index = shard_index.model_copy(
        update={
            "learning_steps": [mutated_first_shard, *shard_index.learning_steps[1:]]
        }
    )
    mutated_manifest = manifest.model_copy(update={"shard_index": mutated_shard_index})

    store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    dataset = await SceneOpsDataset.open(
        learning_manifest=mutated_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=store,
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    with pytest.raises(ShardIndexMismatchError):
        await dataset.get_window(
            dropped_ref, 0, dataset.get_episode(dropped_ref).step_count, projection
        )


# ----------------------------------------------------------------------
# v1/v2 logical-result equivalence
# ----------------------------------------------------------------------


async def test_v2_result_equivalent_to_legacy_v1_reader(tmp_path):
    v2_dataset, _store, _artifacts = await _open_counted(
        tmp_path / "v2", MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    v1_dataset = await _open_legacy(tmp_path / "v1", MULTI_SHARD_SPEC)

    projection = feature_projection_for(MULTI_SHARD_SPEC)
    assert set(v1_dataset.episodes()) == set(v2_dataset.episodes())

    for ref in v2_dataset.episodes():
        step_count = v2_dataset.get_episode(ref).step_count
        v1_window = await v1_dataset.get_window(ref, 0, step_count, projection)
        v2_window = await v2_dataset.get_window(ref, 0, step_count, projection)
        assert v1_window.observation == v2_window.observation
        assert v1_window.action == v2_window.action
        assert v1_window.timestamps_us == v2_window.timestamps_us

        v1_step = await v1_dataset.project_step(ref, 1, projection)
        v2_step = await v2_dataset.project_step(ref, 1, projection)
        assert v1_step.observation == v2_step.observation
        assert v1_step.action == v2_step.action


# ----------------------------------------------------------------------
# Multi-revision, window ordering, ABSENT/MISSING preservation
# ----------------------------------------------------------------------


async def test_multiple_revisions_resolve_independently_via_v2(tmp_path):
    dataset, _store, artifacts = await _open_counted(tmp_path, VARIED_SPEC)
    multi_rev_index = next(
        i
        for i in range(VARIED_SPEC.num_episodes)
        if len(episode_revisions(VARIED_SPEC, i)) > 1
    )
    ref0 = episode_ref(VARIED_SPEC, multi_rev_index, 0)
    ref1 = episode_ref(VARIED_SPEC, multi_rev_index, 1)
    assert ref0.episode_id == ref1.episode_id
    assert ref0.aligned_artifact_checksum != ref1.aligned_artifact_checksum

    projection = artifacts.feature_projection
    window0 = await dataset.get_window(
        ref0, 0, dataset.get_episode(ref0).step_count, projection
    )
    window1 = await dataset.get_window(
        ref1, 0, dataset.get_episode(ref1).step_count, projection
    )
    assert window0.observation != window1.observation
    assert window0.observation[0] == pytest.approx(
        expected_observation(VARIED_SPEC, multi_rev_index, 0, 0)
    )
    assert window1.observation[0] == pytest.approx(
        expected_observation(VARIED_SPEC, multi_rev_index, 0, 1)
    )


async def test_window_ordering_and_timestamps_preserved_via_v2(tmp_path):
    dataset, _store, artifacts = await _open_counted(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    ref = dataset.episodes()[3]
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    step_count = dataset.get_episode(ref).step_count

    window = await dataset.get_window(ref, 0, step_count, projection)
    assert list(window.timestamps_us) == sorted(window.timestamps_us)
    assert len(set(window.timestamps_us)) == step_count  # strictly distinct per step

    ep_idx = int(ref.episode_id.split("-")[-1])
    for step_index in range(step_count):
        assert window.observation[step_index] == pytest.approx(
            expected_observation(MULTI_SHARD_SPEC, ep_idx, step_index)
        )


async def test_absent_and_missing_preserved_through_v2_round_trip(tmp_path):
    dataset, _store, artifacts = await _open_counted(tmp_path, VARIED_SPEC)
    core_channels = set(VARIED_SPEC.core_observation_channels) | set(
        VARIED_SPEC.core_action_channels
    )
    extra_channels = (
        set(VARIED_SPEC.observation_channels) | set(VARIED_SPEC.action_channels)
    ) - core_channels
    assert extra_channels

    saw_absent = False
    saw_missing = False
    for episode_index in range(VARIED_SPEC.num_episodes):
        for revision in episode_revisions(VARIED_SPEC, episode_index):
            ref = episode_ref(VARIED_SPEC, episode_index, revision)
            step_count = dataset.get_episode(ref).step_count
            for step_index in range(step_count):
                step = await dataset.get_step(ref, step_index)
                for channel in core_channels:
                    signal = step.observations.get(channel) or step.actions.get(channel)
                    assert signal is not None
                    assert signal.status == AlignedSignalStatus.RESOLVED
                for channel in extra_channels:
                    signal = step.observations.get(channel) or step.actions.get(channel)
                    if signal is None:
                        saw_absent = True
                    elif signal.status == AlignedSignalStatus.MISSING:
                        saw_missing = True

    assert saw_absent
    assert saw_missing
