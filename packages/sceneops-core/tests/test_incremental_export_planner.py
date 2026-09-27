"""Tests for the pure incremental export planner (SceneOps V2 Request 5.5
§4). No I/O -- everything here is in-memory manifest/plan objects."""

from __future__ import annotations

import pytest

from sceneops_core.episodes.learning import EpisodeRef
from sceneops_core.episodes.learning_export import (
    AlignedArtifactRevision,
    IncrementalExportOverlapError,
    IncrementalExportUnsupportedBaseError,
    LearningDataExportManifest,
    LearningDataShard,
    LearningDataShardIndex,
    ShardEpisodeMember,
    ShardPolicy,
    plan_incremental_export,
)

_POLICY = ShardPolicy(max_episodes_per_shard=200, max_rows_per_shard=200_000)


def _revision(index: int, checksum: str = "c0") -> AlignedArtifactRevision:
    return AlignedArtifactRevision(
        episode_id=f"ep{index}",
        aligned_artifact_id=f"art{index}",
        aligned_artifact_checksum=checksum,
    )


def _member(index: int, checksum: str = "c0") -> ShardEpisodeMember:
    return ShardEpisodeMember(
        episode_ref=EpisodeRef(
            episode_id=f"ep{index}", aligned_artifact_checksum=checksum
        ),
        row_group_index=index,
        row_count=10,
    )


def _shard(shard_index: int, *indices: int) -> LearningDataShard:
    members = [_member(i) for i in indices]
    return LearningDataShard(
        shard_index=shard_index,
        uri=f"mem://shard-{shard_index}",
        checksum=f"sha256:shard-{shard_index}",
        size_bytes=1000,
        row_count=10 * len(members),
        episodes=members,
    )


def _base_manifest(
    *, shard_index: LearningDataShardIndex | None, inputs: list[AlignedArtifactRevision]
) -> LearningDataExportManifest:
    return LearningDataExportManifest(
        export_id="base1",
        dataset_id="d1",
        dataset_version="v1",
        inputs=inputs,
        shard_index=shard_index,
    )


def test_plan_reuses_base_shards_and_computes_new_refs() -> None:
    shard = _shard(0, 0, 1)
    base = _base_manifest(
        shard_index=LearningDataShardIndex(
            shard_policy=_POLICY, learning_steps=[shard], learning_signals=[shard]
        ),
        inputs=[_revision(0), _revision(1)],
    )
    target = [_revision(0), _revision(1), _revision(2)]

    plan = plan_incremental_export(base, target)

    assert plan.base_export_id == "base1"
    assert plan.reused_learning_steps_shards == (shard,)
    assert plan.reused_learning_signals_shards == (shard,)
    assert plan.new_episode_refs == (
        EpisodeRef(episode_id="ep2", aligned_artifact_checksum="c0"),
    )
    assert plan.reused_episode_refs == (
        EpisodeRef(episode_id="ep0", aligned_artifact_checksum="c0"),
        EpisodeRef(episode_id="ep1", aligned_artifact_checksum="c0"),
    )
    assert plan.start_shard_index == 1


def test_plan_is_deterministic() -> None:
    shard = _shard(0, 0, 1)
    base = _base_manifest(
        shard_index=LearningDataShardIndex(
            shard_policy=_POLICY, learning_steps=[shard], learning_signals=[shard]
        ),
        inputs=[_revision(0), _revision(1)],
    )
    target = [_revision(0), _revision(1), _revision(2)]

    assert plan_incremental_export(base, target) == plan_incremental_export(
        base, target
    )


def test_plan_supports_new_revision_of_existing_episode_id() -> None:
    """A new aligned revision of episode_id=ep0 (a different checksum) is a
    genuinely new EpisodeRef, coexisting with the original -- never
    dedup-by-episode_id-alone (SceneOps V2 Request 5.5 explicit requirement).
    """
    shard = _shard(0, 0)
    base = _base_manifest(
        shard_index=LearningDataShardIndex(
            shard_policy=_POLICY, learning_steps=[shard], learning_signals=[shard]
        ),
        inputs=[_revision(0, checksum="c0")],
    )
    target = [_revision(0, checksum="c0"), _revision(0, checksum="c1")]

    plan = plan_incremental_export(base, target)

    assert plan.new_episode_refs == (
        EpisodeRef(episode_id="ep0", aligned_artifact_checksum="c1"),
    )


def test_plan_rejects_v1_single_file_base() -> None:
    base = _base_manifest(shard_index=None, inputs=[_revision(0)])
    target = [_revision(0), _revision(1)]

    with pytest.raises(IncrementalExportUnsupportedBaseError):
        plan_incremental_export(base, target)


def test_plan_rejects_target_missing_a_base_episode() -> None:
    shard = _shard(0, 0, 1)
    base = _base_manifest(
        shard_index=LearningDataShardIndex(
            shard_policy=_POLICY, learning_steps=[shard], learning_signals=[shard]
        ),
        inputs=[_revision(0), _revision(1)],
    )
    target = [_revision(0), _revision(2)]  # drops ep1 -- unsupported removal

    with pytest.raises(IncrementalExportUnsupportedBaseError):
        plan_incremental_export(base, target)


def test_plan_rejects_duplicate_target_keys() -> None:
    shard = _shard(0, 0)
    base = _base_manifest(
        shard_index=LearningDataShardIndex(
            shard_policy=_POLICY, learning_steps=[shard], learning_signals=[shard]
        ),
        inputs=[_revision(0)],
    )
    target = [_revision(0), _revision(1), _revision(1)]

    with pytest.raises(IncrementalExportOverlapError):
        plan_incremental_export(base, target)


def test_plan_rejects_no_op_target() -> None:
    shard = _shard(0, 0)
    base = _base_manifest(
        shard_index=LearningDataShardIndex(
            shard_policy=_POLICY, learning_steps=[shard], learning_signals=[shard]
        ),
        inputs=[_revision(0)],
    )
    target = [_revision(0)]

    with pytest.raises(ValueError, match="adds nothing new"):
        plan_incremental_export(base, target)
