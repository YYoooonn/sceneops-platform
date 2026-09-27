"""Unit coverage for BoundedCache (SceneOps V2 Request 5.4 §2) -- pure,
no I/O, no SceneOpsDataset involved.
"""

from __future__ import annotations

from sceneops_analytics.learning_dataset.lru_cache import BoundedCache


def test_unbounded_cache_never_evicts():
    cache: BoundedCache[int, str] = BoundedCache(max_size=None)
    for i in range(1000):
        cache.put(i, str(i))
    assert len(cache) == 1000
    assert cache.get(0) == "0"
    assert cache.get(999) == "999"


def test_bounded_cache_evicts_least_recently_used():
    cache: BoundedCache[int, str] = BoundedCache(max_size=3)
    cache.put(1, "a")
    cache.put(2, "b")
    cache.put(3, "c")
    cache.put(4, "d")  # evicts 1 (oldest)

    assert len(cache) == 3
    assert cache.get(1) is None
    assert cache.get(2) == "b"
    assert cache.get(3) == "c"
    assert cache.get(4) == "d"


def test_get_refreshes_recency():
    cache: BoundedCache[int, str] = BoundedCache(max_size=2)
    cache.put(1, "a")
    cache.put(2, "b")
    cache.get(1)  # 1 is now more recently used than 2
    cache.put(3, "c")  # evicts 2, not 1

    assert cache.get(1) == "a"
    assert cache.get(2) is None
    assert cache.get(3) == "c"


def test_put_existing_key_updates_value_and_recency():
    cache: BoundedCache[int, str] = BoundedCache(max_size=2)
    cache.put(1, "a")
    cache.put(2, "b")
    cache.put(1, "a-updated")  # refreshes 1's recency
    cache.put(3, "c")  # evicts 2, not 1

    assert cache.get(1) == "a-updated"
    assert cache.get(2) is None


def test_disabled_cache_never_stores_anything():
    cache: BoundedCache[int, str] = BoundedCache(max_size=0)
    cache.put(1, "a")
    assert len(cache) == 0
    assert cache.get(1) is None
    assert 1 not in cache


def test_zero_size_after_construction_is_valid_and_negative_rejected():
    BoundedCache(max_size=0)  # should not raise
    try:
        BoundedCache(max_size=-1)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_stats_track_hits_misses_and_evictions():
    cache: BoundedCache[int, str] = BoundedCache(max_size=2)
    cache.get(1)  # miss
    cache.put(1, "a")
    cache.get(1)  # hit
    cache.put(2, "b")
    cache.put(3, "c")  # evicts 1

    assert cache.stats.misses == 1
    assert cache.stats.hits == 1
    assert cache.stats.evictions == 1
    assert cache.stats.hit_rate == 0.5
