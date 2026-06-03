from __future__ import annotations

from fastapi import HTTPException, status

from sceneops_core.datasets.schemas import (
    RawLogManifest,
    SceneSegmentListResponse,
    SceneSegmentManifest,
)
from sceneops_db.datasets import DatasetVersionRepository
from sceneops_storage import ArtifactStore


class SceneBuildingService:
    def __init__(
        self,
        *,
        version_repository: DatasetVersionRepository,
        artifact_store: ArtifactStore,
        root_uri: str,
    ) -> None:
        self._version_repository = version_repository
        self._artifact_store = artifact_store
        self._root_uri = root_uri.rstrip("/")

    async def get_raw_log(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> RawLogManifest:
        await self._require_version(dataset_id, dataset_version)

        uri = self._raw_log_manifest_uri(dataset_id, dataset_version)
        if not await self._artifact_store.exists(uri):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raw log not found for {dataset_id}:{dataset_version}. Run build_scenes first.",
            )

        data = await self._artifact_store.read_json(uri)
        return RawLogManifest.model_validate(data)

    async def list_scene_segments(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        channel: str | None = None,
        valid_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> SceneSegmentListResponse:
        await self._require_version(dataset_id, dataset_version)

        uri = self._scene_segments_uri(dataset_id, dataset_version)
        if not await self._artifact_store.exists(uri):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene segments not found for {dataset_id}:{dataset_version}. Run build_scenes first.",
            )

        data = await self._artifact_store.read_json(uri)
        segments = [
            SceneSegmentManifest.model_validate(s) for s in data.get("segments", [])
        ]

        if channel is not None:
            segments = [s for s in segments if channel in s.channels]

        if valid_only:
            segments = [
                s
                for s in segments
                if s.quality_summary.get("is_timestamp_gap_within_policy", True)
            ]

        total = len(segments)
        page = segments[offset : offset + limit]

        return SceneSegmentListResponse(
            segments=page,
            count=len(page),
            total=total,
        )

    async def get_scene_segment(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        segment_id: str,
    ) -> SceneSegmentManifest:
        await self._require_version(dataset_id, dataset_version)

        uri = self._scene_segments_uri(dataset_id, dataset_version)
        if not await self._artifact_store.exists(uri):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene segments not found for {dataset_id}:{dataset_version}.",
            )

        data = await self._artifact_store.read_json(uri)
        for raw in data.get("segments", []):
            if (
                raw.get("segmentId") == segment_id
                or raw.get("segment_id") == segment_id
            ):
                return SceneSegmentManifest.model_validate(raw)

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment {segment_id} not found.",
        )

    # ── URI helpers (mirror DatasetArtifactStore in the worker) ──────────────

    def _version_root_uri(self, dataset_id: str, dataset_version: str) -> str:
        return self._artifact_store.join_uri(
            self._root_uri, dataset_id, "versions", dataset_version
        )

    def _raw_log_manifest_uri(self, dataset_id: str, dataset_version: str) -> str:
        return self._artifact_store.join_uri(
            self._version_root_uri(dataset_id, dataset_version),
            "raw",
            "raw_log.json",
        )

    def _scene_segments_uri(self, dataset_id: str, dataset_version: str) -> str:
        return self._artifact_store.join_uri(
            self._version_root_uri(dataset_id, dataset_version),
            "raw",
            "scene_segments.json",
        )

    async def _require_version(self, dataset_id: str, dataset_version: str) -> None:
        try:
            await self._version_repository.get(
                dataset_id=dataset_id,
                version=dataset_version,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dataset version not found: {dataset_id}:{dataset_version}",
            )
