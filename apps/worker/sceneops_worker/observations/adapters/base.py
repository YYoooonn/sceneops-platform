from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.observations.schemas import RawLogFrameIndex, RawLogManifest


@runtime_checkable
class RawLogAdapter(Protocol):
    """Reads a raw log source and produces RawLogManifest + RawLogFrameIndex."""

    async def build_raw_log(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        raw_log_id: str,
        version_root_uri: str,
        params: dict,
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        """Return (manifest, frame_index, manifest_uri, frame_index_uri)."""
        ...
