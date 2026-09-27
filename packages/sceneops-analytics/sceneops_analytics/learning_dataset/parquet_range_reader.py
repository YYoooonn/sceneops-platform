"""Selective Parquet row-group reads over ``ArtifactStore.read_range``
(SceneOps V2 Request 5.3).

::

    (uri, size)  -- size already known from LearningDataShard.size_bytes,
                    never a separate stat/HEAD call
            -> asyncio.to_thread(_read_row_group_sync, ..., loop=<caller's
               already-running event loop, captured before entering the
               thread>)
                    -> _LazyRangeFile (synchronous, seekable file-like;
                       fetches whatever byte range PyArrow asks for, on
                       demand, via ArtifactStore.read_range bridged
                       through asyncio.run_coroutine_threadsafe(...,
                       loop).result() -- schedules the coroutine back onto
                       the caller's own already-running loop instead of
                       spinning up a fresh one per range)
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
every case PyArrow actually exercises.

That version also bridged each ``.read()`` call with a fresh
``asyncio.run()`` per range -- SceneOps V2 Request 5.4 §6 measured this as
material overhead (~200us/call against local disk, ~1.4ms/call -- +58% on
top of a ~2.4ms round trip -- against real MinIO, since ``asyncio.run()``
spins up a brand-new event loop *and* a brand-new default thread-pool
executor every single call, and S3ArtifactStore's own
``asyncio.to_thread`` then nests a second executor inside that). Replaced
with ``run_coroutine_threadsafe`` against the *caller's* already-running
event loop (captured once, before entering the worker thread) -- no new
loop, no new executor, per range read.
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


def _row_group_byte_span(row_group: pq.RowGroupMetaData) -> tuple[int, int]:
    """The exact contiguous byte range in the shard file covering every
    column chunk in this row group -- computed from real column-chunk
    offsets/sizes (``dictionary_page_offset`` when a column has a
    dictionary page, else ``data_page_offset``; ``total_compressed_size``
    is measured from whichever page starts the chunk). ``column.
    file_offset`` is not used -- an unreliable/legacy Thrift field PyArrow's
    writer leaves at 0. Used only by the bulk path below (SceneOps V2
    Request 5.4 §3/§4) to pre-warm one combined read spanning several row
    groups; the single-row-group selective path (``read_episode_row_group``)
    never needs this -- PyArrow's own lazy reads already land exactly on
    one row group's bytes without it."""
    starts: list[int] = []
    ends: list[int] = []
    for i in range(row_group.num_columns):
        column = row_group.column(i)
        start = (
            column.dictionary_page_offset
            if column.dictionary_page_offset and column.dictionary_page_offset > 0
            else column.data_page_offset
        )
        starts.append(start)
        ends.append(start + column.total_compressed_size)
    return min(starts), max(ends)


def _sync_read_range(
    artifact_store: ArtifactStore,
    uri: str,
    offset: int,
    length: int,
    loop: asyncio.AbstractEventLoop,
) -> bytes:
    """Bridges one async ``ArtifactStore.read_range`` call into the
    synchronous world PyArrow's file-like protocol requires -- by
    scheduling it onto ``loop`` (the *caller's* already-running event
    loop, captured before entering this worker thread) via
    ``run_coroutine_threadsafe`` and blocking on the result. Cheap: no new
    event loop, no new thread-pool executor, reuses whatever the caller's
    loop already has running (SceneOps V2 Request 5.4 §6 -- see this
    module's own header for the measured cost of the ``asyncio.run()``
    approach this replaced)."""
    future = asyncio.run_coroutine_threadsafe(
        artifact_store.read_range(uri, offset, length), loop
    )
    return future.result()


def _read_row_group_sync(
    artifact_store: ArtifactStore,
    uri: str,
    size: int,
    row_group_index: int,
    cached_metadata: pq.FileMetaData | None,
    loop: asyncio.AbstractEventLoop,
) -> tuple[pa.Table, pq.FileMetaData, list[tuple[int, int]]]:
    file_obj = _LazyRangeFile(
        lambda offset, length: _sync_read_range(
            artifact_store, uri, offset, length, loop
        ),
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
    loop = asyncio.get_running_loop()
    table, metadata, _fetched_ranges = await asyncio.to_thread(
        _read_row_group_sync,
        artifact_store,
        uri,
        size,
        row_group_index,
        cached_metadata,
        loop,
    )
    return table, metadata


def _read_row_groups_bulk_sync(
    artifact_store: ArtifactStore,
    uri: str,
    size: int,
    row_group_indices: list[int],
    cached_metadata: pq.FileMetaData | None,
    loop: asyncio.AbstractEventLoop,
) -> tuple[dict[int, pa.Table], pq.FileMetaData, list[tuple[int, int]]]:
    file_obj = _LazyRangeFile(
        lambda offset, length: _sync_read_range(
            artifact_store, uri, offset, length, loop
        ),
        total_size=size,
    )
    try:
        if cached_metadata is not None:
            metadata = cached_metadata
        else:
            metadata = pq.ParquetFile(file_obj).metadata

        # One combined read spanning every requested row group's bytes,
        # pre-warming _LazyRangeFile's window cache (SceneOps V2 Request
        # 5.4 §3/§4) -- the per-row-group reads below are then served
        # entirely from memory, turning N small reads into 1 (per shard,
        # not per episode).
        spans = [_row_group_byte_span(metadata.row_group(i)) for i in row_group_indices]
        combined_start = min(start for start, _end in spans)
        combined_end = max(end for _start, end in spans)
        file_obj.seek(combined_start)
        file_obj.read(combined_end - combined_start)

        parquet_file = pq.ParquetFile(file_obj, metadata=metadata)
        tables = {i: parquet_file.read_row_group(i) for i in row_group_indices}
    except Exception as exc:  # noqa: BLE001 -- surfaced as one clear error type
        raise ShardMetadataError(f"{uri}: {exc}") from exc
    return tables, metadata, file_obj.fetched_ranges


async def read_shard_row_groups_bulk(
    artifact_store: ArtifactStore,
    uri: str,
    size: int,
    row_group_indices: list[int],
    *,
    cached_metadata: pq.FileMetaData | None = None,
) -> tuple[dict[int, pa.Table], pq.FileMetaData]:
    """Fetch multiple row groups from one shard with as few network/disk
    reads as possible (SceneOps V2 Request 5.4 §3/§4): one combined range
    covering every requested row group's bytes (plus a footer fetch if
    ``cached_metadata`` isn't already known), instead of one
    ``read_episode_row_group`` call per row group. For known bulk callers
    that already know they need most/all of a shard's episodes
    (``SceneOpsDataset.preload_episodes``/``SequenceSampler.create()``) --
    not a generic query optimizer, and not used by the default
    single-EpisodeRef selective path, which stays exactly as it was in
    Request 5.3.

    Returns ``(tables_by_row_group_index, metadata)`` -- ``tables`` has
    exactly one entry per requested index, keyed by that index.
    """
    loop = asyncio.get_running_loop()
    tables, metadata, _fetched_ranges = await asyncio.to_thread(
        _read_row_groups_bulk_sync,
        artifact_store,
        uri,
        size,
        row_group_indices,
        cached_metadata,
        loop,
    )
    return tables, metadata


__all__ = [
    "ShardMetadataError",
    "read_episode_row_group",
    "read_shard_row_groups_bulk",
]
