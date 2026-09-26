"""Selective Parquet row-group reads over ``ArtifactStore.read_range``
(SceneOps V2 Request 5.3).

::

    (uri, size)  -- size already known from LearningDataShard.size_bytes,
                    never a separate stat/HEAD call
            -> asyncio.to_thread(_read_row_group_sync, ...)
                    -> _LazyRangeFile (synchronous, seekable file-like;
                       fetches whatever byte range PyArrow asks for, on
                       demand, via ArtifactStore.read_range bridged
                       through asyncio.run() -- safe here because this
                       runs inside a plain worker thread with no event
                       loop of its own)
                    -> pyarrow.parquet.ParquetFile(file, metadata=cached)
                       -- PyArrow itself decides what to read: footer only
                       (first open) or nothing at all (metadata supplied),
                       then exactly the target row group's column chunks
                    -> .read_row_group(i)

An earlier version of this module tried to precompute exact byte windows
(footer trailer, footer body, target row-group span) from manual Parquet
layout math and hand PyArrow only those pre-fetched buffers. That broke
for small shards: PyArrow's reader slurps small files whole rather than
seeking, which is a reasonable internal optimization but not something
this module should have to predict. Lazy, on-demand fetching handles
every case PyArrow actually exercises, at the cost of each individual
``.read()`` call crossing the async boundary via its own ``asyncio.run()``
-- acceptable since PyArrow issues a small, bounded number of reads per
row-group access (typically 2-4), not one per row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pyarrow as pa
import pyarrow.parquet as pq

from sceneops_core.artifacts.contracts import ArtifactStore


class ShardMetadataError(Exception):
    """The shard's Parquet footer/row-group data could not be parsed
    (corrupt file, or a size mismatch against the manifest's recorded
    ``size_bytes``) -- never expected in normal operation."""


class _LazyRangeFile:
    """Synchronous, seekable file-like object that fetches whatever byte
    range is requested, on demand, via a synchronous ``fetch_range``
    callback -- no precomputed windows, no guessing what PyArrow will ask
    for. Every fetched range is memoized (``self._windows``): if a later
    request falls entirely within an already-fetched window, it is served
    from memory instead of re-fetched -- without this, small files (where
    PyArrow's initial footer-area read and the target row group's read
    can overlap heavily, or even fully contain each other) would fetch the
    same bytes twice. ``self.fetched_ranges`` records only genuine network
    fetches (post-dedup), purely for test/benchmark instrumentation."""

    def __init__(
        self, fetch_range: Callable[[int, int], bytes], *, total_size: int
    ) -> None:
        self._fetch_range = fetch_range
        self._total_size = total_size
        self._pos = 0
        self._windows: list[tuple[int, bytes]] = []
        self.fetched_ranges: list[tuple[int, int]] = []

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._total_size + offset
        else:
            raise ValueError(f"unsupported whence={whence}")
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, n: int | None = -1) -> bytes:
        start = self._pos
        end = self._total_size if (n is None or n < 0) else start + n
        end = min(end, self._total_size)
        if end <= start:
            return b""

        for window_start, window_bytes in self._windows:
            window_end = window_start + len(window_bytes)
            if window_start <= start and end <= window_end:
                data = window_bytes[start - window_start : end - window_start]
                self._pos = end
                return data

        length = end - start
        self.fetched_ranges.append((start, length))
        data = self._fetch_range(start, length)
        self._windows.append((start, data))
        self._pos = start + len(data)
        return data

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return False

    def close(self) -> None:
        pass


def _sync_read_range(
    artifact_store: ArtifactStore, uri: str, offset: int, length: int
) -> bytes:
    """Bridges one async ``ArtifactStore.read_range`` call into the
    synchronous world PyArrow's file-like protocol requires. Only ever
    called from inside a worker thread with no event loop of its own
    (``asyncio.to_thread`` in ``read_episode_row_group`` below), so a
    fresh ``asyncio.run()`` per call is safe -- it cannot conflict with a
    loop already running in the calling (main) thread."""
    return asyncio.run(artifact_store.read_range(uri, offset, length))


def _read_row_group_sync(
    artifact_store: ArtifactStore,
    uri: str,
    size: int,
    row_group_index: int,
    cached_metadata: pq.FileMetaData | None,
) -> tuple[pa.Table, pq.FileMetaData, list[tuple[int, int]]]:
    file_obj = _LazyRangeFile(
        lambda offset, length: _sync_read_range(artifact_store, uri, offset, length),
        total_size=size,
    )
    try:
        parquet_file = pq.ParquetFile(file_obj, metadata=cached_metadata)
        table = parquet_file.read_row_group(row_group_index)
    except Exception as exc:  # noqa: BLE001 -- surfaced as one clear error type
        raise ShardMetadataError(f"{uri}: {exc}") from exc
    return table, parquet_file.metadata, file_obj.fetched_ranges


async def read_episode_row_group(
    artifact_store: ArtifactStore,
    uri: str,
    size: int,
    row_group_index: int,
    *,
    cached_metadata: pq.FileMetaData | None = None,
) -> tuple[pa.Table, pq.FileMetaData]:
    """Fetch exactly one row group's rows from ``uri`` -- PyArrow decides
    the exact byte ranges (footer, if ``cached_metadata`` is not already
    supplied, then the target row group's column chunks); this function
    never fetches the whole shard itself. Runs in a worker thread
    (``asyncio.to_thread``) since PyArrow's Parquet reader is a blocking
    C++ call. Returns ``(table, metadata)`` -- callers should cache
    ``metadata`` per shard and pass it back as ``cached_metadata`` on
    subsequent calls to skip re-fetching the footer entirely."""
    table, metadata, _fetched_ranges = await asyncio.to_thread(
        _read_row_group_sync,
        artifact_store,
        uri,
        size,
        row_group_index,
        cached_metadata,
    )
    return table, metadata


__all__ = ["ShardMetadataError", "read_episode_row_group"]
