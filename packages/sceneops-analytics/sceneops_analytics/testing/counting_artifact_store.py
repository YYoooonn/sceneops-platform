"""CountingArtifactStore: a thin ArtifactStore wrapper that records call
counts and byte volumes per URI (SceneOps V2 Request 5.1) -- used only by
the learning-data scaling benchmark harness
(``scripts/dev/benchmark_learning_data_scaling.py``) to answer "how many
files, and how many bytes, did this workload actually touch" without
guessing from Parquet file sizes on disk.

Test/benchmark-support code only -- never imported by production code, and
adds no retry/caching/backoff behavior of its own; every call is forwarded
unchanged to the wrapped ArtifactStore after recording.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.common.schemas import ArtifactUri


@dataclass
class IoStats:
    """Cumulative counters since construction or the last ``reset()``."""

    read_bytes_calls: int = 0
    read_bytes_total: int = 0
    write_bytes_calls: int = 0
    write_bytes_total: int = 0
    per_uri_read_bytes: Counter[str] = field(default_factory=Counter)
    per_uri_read_calls: Counter[str] = field(default_factory=Counter)

    def reset(self) -> None:
        self.read_bytes_calls = 0
        self.read_bytes_total = 0
        self.write_bytes_calls = 0
        self.write_bytes_total = 0
        self.per_uri_read_bytes.clear()
        self.per_uri_read_calls.clear()

    @property
    def distinct_uris_read(self) -> int:
        return len(self.per_uri_read_calls)


class CountingArtifactStore(ArtifactStore):
    """Wraps any ArtifactStore, forwarding every call unchanged while
    recording ``read_bytes``/``write_bytes`` volume in ``self.stats``. Every
    other ArtifactStore method (``exists``/``read_json``/``write_json``/
    ``list_json``/``delete_prefix``/``public_url``/``join_uri``) is
    forwarded without counting -- the learning-data access path this
    benchmark cares about never calls those for step/signal data."""

    def __init__(self, inner: ArtifactStore) -> None:
        self._inner = inner
        self.stats = IoStats()

    def join_uri(self, root: ArtifactUri, *parts: str) -> ArtifactUri:
        return self._inner.join_uri(root, *parts)

    async def exists(self, uri: ArtifactUri) -> bool:
        return await self._inner.exists(uri)

    async def read_json(self, uri: ArtifactUri):
        return await self._inner.read_json(uri)

    async def write_json(self, uri: ArtifactUri, payload) -> None:
        await self._inner.write_json(uri, payload)

    async def read_bytes(self, uri: ArtifactUri) -> bytes:
        data = await self._inner.read_bytes(uri)
        self.stats.read_bytes_calls += 1
        self.stats.read_bytes_total += len(data)
        self.stats.per_uri_read_bytes[uri] += len(data)
        self.stats.per_uri_read_calls[uri] += 1
        return data

    async def write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        self.stats.write_bytes_calls += 1
        self.stats.write_bytes_total += len(data)
        await self._inner.write_bytes(uri, data)

    async def list_json(self, uri: ArtifactUri) -> list[ArtifactUri]:
        return await self._inner.list_json(uri)

    async def delete_prefix(self, uri: ArtifactUri) -> None:
        await self._inner.delete_prefix(uri)

    def public_url(self, uri: ArtifactUri) -> str:
        return self._inner.public_url(uri)


__all__ = ["CountingArtifactStore", "IoStats"]
