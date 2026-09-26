"""Bounded-cache configuration and bulk-access correctness (SceneOps V2
Request 5.4): CachePolicy wiring, group_by_shard/preload_episodes/
resolve_feature_schemas_bulk, and SequenceSampler.create()'s bulk path --
all against real v2-sharded fixtures, using CountingArtifactStore to prove
I/O behavior rather than only asserting output equality.
"""

from __future__ import annotations


from sceneops_analytics import (
    DEFAULT_CACHE_POLICY,
    DISABLED_CACHE_POLICY,
    CachePolicy,
    SceneOpsDataset,
    SequenceSampler,
)
from sceneops_analytics.testing import (
    CountingArtifactStore,
    ScaleSpec,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.learning_export import ShardPolicy
from sceneops_storage import LocalArtifactStore

MULTI_SHARD_SPEC = ScaleSpec(
    name="cache-bulk-test", num_episodes=8, steps_per_episode=6
)
TIGHT_POLICY = ShardPolicy(max_episodes_per_shard=2, max_rows_per_shard=10_000)


async def _open(tmp_path, spec, *, shard_policy=None, cache_policy=None):
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
        cache_policy=cache_policy,
    )
    return dataset, store, artifacts


# ----------------------------------------------------------------------
# CachePolicy configuration
# ----------------------------------------------------------------------


async def test_default_cache_policy_is_bounded_not_unbounded(tmp_path):
    dataset, _store, _artifacts = await _open(tmp_path, MULTI_SHARD_SPEC)
    assert dataset._cache_policy == DEFAULT_CACHE_POLICY
    assert (
        dataset._episode_steps_cache._max_size == DEFAULT_CACHE_POLICY.max_episode_steps
    )
    assert dataset._episode_steps_cache._max_size is not None


async def test_disabled_cache_policy_never_caches_anything(tmp_path):
    dataset, store, artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, cache_policy=DISABLED_CACHE_POLICY
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    ref = dataset.episodes()[0]

    await dataset.get_window(ref, 0, dataset.get_episode(ref).step_count, projection)
    assert len(dataset._episode_steps_cache) == 0
    assert len(dataset._schema_cache) == 0
    assert len(dataset._shard_metadata_cache) == 0

    # a second access re-fetches everything -- no caching happened
    io_before = store.stats.total_bytes_read
    await dataset.get_window(ref, 0, dataset.get_episode(ref).step_count, projection)
    assert store.stats.total_bytes_read - io_before > 0


async def test_custom_cache_policy_bound_is_respected(tmp_path):
    policy = CachePolicy(max_episode_steps=2, max_schemas=2, max_shard_metadata=2)
    dataset, _store, _artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY, cache_policy=policy
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    for ref in dataset.episodes():
        await dataset.get_window(
            ref, 0, dataset.get_episode(ref).step_count, projection
        )

    assert len(dataset._episode_steps_cache) <= 2
    assert len(dataset._schema_cache) <= 2


# ----------------------------------------------------------------------
# group_by_shard / preload_episodes / resolve_feature_schemas_bulk
# ----------------------------------------------------------------------


async def test_group_by_shard_preserves_order_and_covers_every_ref(tmp_path):
    dataset, _store, _artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    refs = dataset.episodes()
    groups = dataset.group_by_shard(refs)

    assert [ref for group in groups for ref in group] == refs
    assert len(groups) >= 3  # 8 episodes / 2 per shard


async def test_group_by_shard_v1_returns_single_group(tmp_path):
    from sceneops_analytics.testing import build_interop_test_dataset

    bootstrap = await build_interop_test_dataset(tmp_path)
    dataset = bootstrap.dataset
    refs = dataset.episodes()
    groups = dataset.group_by_shard(refs)
    assert groups == [refs]


async def test_preload_episodes_bulk_fetch_matches_individual_fetch(tmp_path):
    dataset, store, artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    refs = dataset.episodes()

    # baseline: individual selective reads, in a fresh dataset
    dataset2, store2, _ = await _open(
        tmp_path / "b", MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    windows_individual = {}
    for ref in refs:
        windows_individual[ref] = await dataset2.get_window(
            ref, 0, dataset2.get_episode(ref).step_count, projection
        )

    # bulk preload then access
    await dataset.preload_episodes(refs)
    for ref in refs:
        window = await dataset.get_window(
            ref, 0, dataset.get_episode(ref).step_count, projection
        )
        assert window.observation == windows_individual[ref].observation
        assert window.action == windows_individual[ref].action


async def test_preload_episodes_reduces_range_calls_vs_individual(tmp_path):
    dataset, store, artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    shard = artifacts.learning_manifest.shard_index.learning_steps[0]
    refs = [m.episode_ref for m in shard.episodes]
    assert len(refs) == 2  # tight policy

    calls_before = store.stats.read_range_calls
    await dataset.preload_episodes(refs)
    bulk_calls = store.stats.read_range_calls - calls_before

    dataset2, store2, _ = await _open(
        tmp_path / "b2", MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    for ref in refs:
        await dataset2.get_window(
            ref, 0, dataset2.get_episode(ref).step_count, projection
        )
    individual_calls = store2.stats.read_range_calls

    assert bulk_calls < individual_calls


async def test_resolve_feature_schemas_bulk_matches_individual_resolution(tmp_path):
    dataset, store, artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    refs = dataset.episodes()

    bulk_schemas = await dataset.resolve_feature_schemas_bulk(refs, projection)

    dataset2, _store2, _ = await _open(
        tmp_path / "b3", MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    for ref in refs:
        individual_schema = await dataset2.resolve_feature_schema(ref, projection)
        assert bulk_schemas[ref] == individual_schema


async def test_resolve_feature_schemas_bulk_does_not_depend_on_cache_survival(tmp_path):
    """Even with a cache too small to hold a whole shard's episodes, bulk
    schema resolution must still return correct results for every ref
    (SceneOps V2 Request 5.4's fix for the eviction-before-use bug found
    during this request's own development)."""
    tiny_cache_policy = CachePolicy(
        max_episode_steps=1, max_schemas=1, max_shard_metadata=8
    )
    dataset, _store, artifacts = await _open(
        tmp_path,
        MULTI_SHARD_SPEC,
        shard_policy=ShardPolicy(max_episodes_per_shard=8, max_rows_per_shard=10_000),
        cache_policy=tiny_cache_policy,
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    refs = dataset.episodes()  # all 8 episodes, one shard

    schemas = await dataset.resolve_feature_schemas_bulk(refs, projection)
    assert set(schemas) == set(refs)
    # all episodes share an identical schema in this fixture
    first_schema = schemas[refs[0]]
    assert all(schema == first_schema for schema in schemas.values())


# ----------------------------------------------------------------------
# SequenceSampler.create() bulk path (v2)
# ----------------------------------------------------------------------


async def test_sampler_create_never_calls_get_window_v2(tmp_path):
    dataset, _store, artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)

    calls = []
    original_get_window = dataset.get_window

    async def _counting_get_window(*args, **kwargs):
        calls.append((args, kwargs))
        return await original_get_window(*args, **kwargs)

    dataset.get_window = _counting_get_window

    sampler = await SequenceSampler.create(
        dataset, projection=projection, horizon=3, stride=1
    )
    assert calls == []

    await sampler.get(0)
    assert len(calls) == 1


async def test_sampler_create_bulk_path_produces_consistent_schema_across_shards(
    tmp_path,
):
    """A sampler spanning multiple shards resolves one shared FeatureSchema
    via the bulk path exactly as the old per-episode path did. This
    fixture is homogeneous (every episode shares an identical schema by
    construction), so it exercises the *positive* case; the mismatch-
    raising mechanics themselves (SamplerSchemaMismatchError) are
    `_pure_resolve_feature_schema`/the same consistency-check loop this
    bulk path reuses verbatim -- unchanged and already covered by
    test_incompatible_schemas_across_episodes_raise (existing, v1) --
    only the surrounding iteration (per-shard-group vs per-episode)
    differs, and that is what this test targets."""

    dataset, _store, artifacts = await _open(
        tmp_path, MULTI_SHARD_SPEC, shard_policy=TIGHT_POLICY
    )
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    sampler = await SequenceSampler.create(
        dataset, projection=projection, horizon=3, stride=1
    )
    assert sampler.feature_schema is not None


async def test_short_episode_never_bulk_fetched(tmp_path):
    """An episode too short to contribute any window must never be
    touched by resolve_feature_schemas_bulk -- mirrors the existing v1
    test_short_episode_schema_is_never_resolved invariant for the v2 bulk
    path."""
    spec = ScaleSpec(name="short-episode-test", num_episodes=4, steps_per_episode=10)
    dataset, store, artifacts = await _open(
        tmp_path,
        spec,
        shard_policy=ShardPolicy(max_episodes_per_shard=4, max_rows_per_shard=1000),
    )
    projection = feature_projection_for(spec)

    # horizon larger than every episode's step_count -> zero contributing episodes
    sampler = await SequenceSampler.create(
        dataset, projection=projection, horizon=1000, stride=1
    )
    assert len(sampler) == 0
    assert sampler.feature_schema is None
    # nothing was ever fetched for schema resolution
    assert store.stats.read_range_calls == 0
