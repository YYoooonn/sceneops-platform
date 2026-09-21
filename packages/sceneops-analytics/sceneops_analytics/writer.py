from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

import polars as pl

from sceneops_storage import ArtifactStore


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic, compact JSON encoding, matching the convention used by
    EpisodeArtifactStore for every other checksummed SceneOps artifact
    (SceneOps V2 Request 2.5, mirroring Request 2.3 §3)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class AnalyticsTableWriteResult:
    """Like EpisodeArtifactStore.EpisodeArtifactWriteResult, but for Parquet
    tables/manifests written through this writer (SceneOps V2 Request 2.5).
    Added alongside the existing str-only write_table/write_robot_run_table
    -- those two keep their existing return type unchanged so
    export_analytics_snapshot/export_robot_analytics_snapshot are
    unaffected; only the new learning-export write paths return this."""

    uri: str
    checksum: str
    size_bytes: int


class AnalyticsTableWriter:
    """Writes Polars tables as Parquet artifacts under an ArtifactStore root.

    Two scoping schemes, one underlying writer:
      - dataset-scoped:  ``{root_uri}/{dataset_id}/{dataset_version}/{table_name}.parquet``
      - robot-run-scoped: ``{root_uri}/robot_runs/{robot_run_id}/{table_name}.parquet``

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
        return await self._write_parquet(uri, df)

    def robot_run_table_uri(self, *, robot_run_id: str, table_name: str) -> str:
        return self.artifact_store.join_uri(
            self.root_uri,
            "robot_runs",
            robot_run_id,
            f"{table_name}.parquet",
        )

    async def write_robot_run_table(
        self,
        table_name: str,
        df: pl.DataFrame,
        *,
        robot_run_id: str,
    ) -> str:
        uri = self.robot_run_table_uri(robot_run_id=robot_run_id, table_name=table_name)
        return await self._write_parquet(uri, df)

    async def _write_parquet(self, uri: str, df: pl.DataFrame) -> str:
        buffer = io.BytesIO()
        df.write_parquet(buffer)
        await self.artifact_store.write_bytes(uri, buffer.getvalue())
        return uri

    # ------------------------------------------------------------------
    # Columnar learning-data export snapshots (SceneOps V2 Request 2.5)
    # ------------------------------------------------------------------
    #
    # Scoped by export_id rather than overwriting a single per-dataset-
    # version URI like write_table does -- multiple learning-data export
    # snapshots must coexist per DatasetVersion (Request 2.5 §6), each
    # identified by its own deterministic export_id.

    def learning_table_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        export_id: str,
        table_name: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.root_uri,
            dataset_id,
            dataset_version,
            "learning",
            export_id[:16],
            f"{table_name}.parquet",
        )

    async def write_learning_table(
        self,
        table_name: str,
        df: pl.DataFrame,
        *,
        dataset_id: str,
        dataset_version: str,
        export_id: str,
    ) -> AnalyticsTableWriteResult:
        uri = self.learning_table_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            table_name=table_name,
        )
        buffer = io.BytesIO()
        df.write_parquet(buffer)
        data = buffer.getvalue()
        await self.artifact_store.write_bytes(uri, data)
        return AnalyticsTableWriteResult(
            uri=uri, checksum=f"sha256:{_sha256_hex(data)}", size_bytes=len(data)
        )

    def learning_export_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        export_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.root_uri,
            dataset_id,
            dataset_version,
            "learning",
            export_id[:16],
            "manifest.json",
        )

    async def write_learning_export_manifest(
        self,
        manifest: Any,
        *,
        dataset_id: str,
        dataset_version: str,
        export_id: str,
    ) -> AnalyticsTableWriteResult:
        uri = self.learning_export_manifest_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
        )
        data = _canonical_bytes(manifest.to_artifact_dict())
        await self.artifact_store.write_bytes(uri, data)
        return AnalyticsTableWriteResult(
            uri=uri, checksum=f"sha256:{_sha256_hex(data)}", size_bytes=len(data)
        )

    async def read_learning_export_manifest_bytes(self, uri: str) -> bytes | None:
        """Raw bytes, for CURATE_EPISODES (SceneOps V2 Request 2.6) which
        needs to hash the exact pinned LearningDataExportManifest content
        before parsing it -- mirrors EpisodeArtifactStore.
        read_aligned_episode_bytes's role for aligned artifacts."""
        if not await self.artifact_store.exists(uri):
            return None
        return await self.artifact_store.read_bytes(uri)

    # ------------------------------------------------------------------
    # Episode curation manifests (SceneOps V2 Request 2.6)
    # ------------------------------------------------------------------
    #
    # Scoped by curation_id, same coexisting-snapshots pattern as
    # learning_export_manifest_uri above -- multiple curation runs must
    # coexist per DatasetVersion, each identified by its own deterministic
    # curation_id.

    def curation_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        curation_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.root_uri,
            dataset_id,
            dataset_version,
            "curation",
            curation_id[:16],
            "manifest.json",
        )

    async def write_curation_manifest(
        self,
        manifest: Any,
        *,
        dataset_id: str,
        dataset_version: str,
        curation_id: str,
    ) -> AnalyticsTableWriteResult:
        uri = self.curation_manifest_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            curation_id=curation_id,
        )
        data = _canonical_bytes(manifest.to_artifact_dict())
        await self.artifact_store.write_bytes(uri, data)
        return AnalyticsTableWriteResult(
            uri=uri, checksum=f"sha256:{_sha256_hex(data)}", size_bytes=len(data)
        )


__all__ = ["AnalyticsTableWriter", "AnalyticsTableWriteResult"]
