"""A minimal bounded LRU cache (SceneOps V2 Request 5.4 §2) -- deliberately
plain (``collections.OrderedDict``, no third-party dependency, no
size-weighted/TTL/generational policy): the measured per-episode memory
footprint (see ``docs/history/learning-data-scaling-baseline.md``
§42/§43) varies by episode width, not in a way that justifies more than a
simple entry-count bound, and the task instructions prefer the simplest
policy the evidence supports.

Never process-global: one instance is constructed per ``SceneOpsDataset``
and lives exactly as long as that dataset object does.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


@dataclass
class CacheStats:
    """Cumulative hit/miss/eviction counters -- benchmarking/instrumentation
    only, never consulted for correctness."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return self.hits / total if total else None


class BoundedCache(Generic[K, V]):
    """LRU-evicting cache with a fixed maximum entry count.

    ``max_size=None`` means unbounded (the pre-Request-5.4 behavior --
    available for callers who know their own working set is already
    small, but never ``SceneOpsDataset.open()``'s own default).
    ``max_size=0`` disables caching entirely: ``put`` is a no-op and
    ``get``/``__contains__`` never report a hit -- useful for
    benchmarking/debugging raw I/O cost without cache effects (Request
    5.4 §2's explicit requirement).
    """

    def __init__(self, max_size: int | None) -> None:
        if max_size is not None and max_size < 0:
            raise ValueError(f"max_size must be >= 0 or None, got {max_size}")
        self._max_size = max_size
        self._data: OrderedDict[K, V] = OrderedDict()
        self.stats = CacheStats()

    def get(self, key: K) -> V | None:
        if key not in self._data:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: K, value: V) -> None:
        if self._max_size == 0:
            return
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        if self._max_size is not None:
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)
                self.stats.evictions += 1

    def __contains__(self, key: K) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)


__all__ = ["BoundedCache", "CacheStats"]
