from __future__ import annotations

import io

import polars as pl

from sceneops_storage import ArtifactStore


class AnalyticsTableWriter:
    """Writes Polars tables as Parquet artifacts under an ArtifactStore root.

    Layout: ``{root_uri}/{dataset_id}/{dataset_version}/{table_name}.parquet``.
    A rebuild overwrites the same URI (same idempotent-rebuild pattern as
    ``DatasetArtifactStore.write_dataset_manifest``) rather than versioning
    each write.
    """

    def __init__(self, *, artifact_store: ArtifactStore, root_uri: str) -> None:
        self.artifact_store = artifact_store
        self.root_uri = root_uri

    def table_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        table_name: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.root_uri,
            dataset_id,
            dataset_version,
            f"{table_name}.parquet",
        )

    async def write_table(
        self,
        table_name: str,
        df: pl.DataFrame,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> str:
        uri = self.table_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            table_name=table_name,
        )

        buffer = io.BytesIO()
        df.write_parquet(buffer)

        await self.artifact_store.write_bytes(uri, buffer.getvalue())
        return uri


__all__ = ["AnalyticsTableWriter"]
