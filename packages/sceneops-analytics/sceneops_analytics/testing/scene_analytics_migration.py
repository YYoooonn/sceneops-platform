"""What the previous Scene analytics schema left in a bucket.

``write_v1_scene_tables`` writes the exact bytes that schema's writer produced for
the same input: no temporal context beyond a clock id, no ``source_kind`` or
``content_id``, no schema stamp, at the key its content names. So a test can show a
reader refusing, and a new snapshot coexisting with, a real old one. It lives here,
and nowhere near production paths, because nothing but a test may produce such a
table.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import polars as pl

from sceneops_core.common.checksums import sha256_checksum

from ..tables import (
    SceneRevisionContent,
    build_keyframes_table,
    build_observations_table,
    build_scenes_table,
)
from ..writer import AnalyticsTableWriter, AnalyticsTableWriteResult

_V2_ONLY_COLUMNS = {
    "scenes": (
        "source_kind",
        "content_id",
        "window_clock_origin",
        "window_clock_scope",
    ),
    "observations": ("clock_origin", "clock_scope"),
    "keyframes": ("clock_origin", "clock_scope"),
}
_BUILDERS = {
    "scenes": build_scenes_table,
    "observations": build_observations_table,
    "keyframes": build_keyframes_table,
}


def v1_scene_table(
    table_name: str,
    *,
    dataset_id: str,
    dataset_version: str,
    scenes: Sequence[SceneRevisionContent],
) -> pl.DataFrame:
    return _BUILDERS[table_name](
        dataset_id=dataset_id, dataset_version=dataset_version, scenes=scenes
    ).drop(_V2_ONLY_COLUMNS[table_name])


def v1_scene_table_bytes(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer)
    return buffer.getvalue()


async def write_v1_scene_tables(
    writer: AnalyticsTableWriter,
    *,
    dataset_id: str,
    dataset_version: str,
    scenes: Sequence[SceneRevisionContent],
) -> dict[str, AnalyticsTableWriteResult]:
    """The three tables of one v1 snapshot, by table name."""
    results: dict[str, AnalyticsTableWriteResult] = {}
    for table_name in _BUILDERS:
        data = v1_scene_table_bytes(
            v1_scene_table(
                table_name,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                scenes=scenes,
            )
        )
        checksum = sha256_checksum(data)
        uri = writer.table_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            table_name=table_name,
            checksum=checksum,
        )
        await writer.artifact_store.write_bytes(uri, data)
        results[table_name] = AnalyticsTableWriteResult(
            uri=uri, checksum=checksum, size_bytes=len(data)
        )
    return results
