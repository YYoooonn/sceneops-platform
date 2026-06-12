from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.common.schemas import ArtifactUri

from sceneops_storage.exceptions import (
    ArtifactNotFoundError,
    ArtifactReadError,
    ArtifactWriteError,
)
from sceneops_storage.uri import join_uri


class LocalArtifactStore(ArtifactStore):
    def __init__(self, *, root_uri: str) -> None:
        self.root_uri = root_uri.rstrip("/")

    def join_uri(self, root: ArtifactUri, *parts: str) -> ArtifactUri:
        return join_uri(root, *parts)

    async def exists(self, uri: ArtifactUri) -> bool:
        return self._to_path(uri).exists()

    async def read_json(self, uri: ArtifactUri) -> Any:
        path = self._to_path(uri)
        if not path.exists():
            raise ArtifactNotFoundError(uri)
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactReadError(f"Failed to read JSON artifact: {uri}") from exc

    async def write_json(self, uri: ArtifactUri, payload: Any) -> None:
        path = self._to_path(uri)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise ArtifactWriteError(f"Failed to write JSON artifact: {uri}") from exc

    async def read_bytes(self, uri: ArtifactUri) -> bytes:
        path = self._to_path(uri)
        if not path.exists():
            raise ArtifactNotFoundError(uri)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ArtifactReadError(f"Failed to read binary artifact: {uri}") from exc

    async def write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        path = self._to_path(uri)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise ArtifactWriteError(f"Failed to write binary artifact: {uri}") from exc

    async def list_json(self, uri: ArtifactUri) -> list[ArtifactUri]:
        path = self._to_path(uri)
        if not path.exists():
            return []
        return [str(item) for item in sorted(path.glob("*.json")) if item.is_file()]

    async def delete_prefix(self, uri: ArtifactUri) -> None:
        path = self._to_path(uri)
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def public_url(self, uri: ArtifactUri) -> str:
        return uri

    def _to_path(self, uri: ArtifactUri) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            return Path(parsed.path)
        if parsed.scheme:
            raise ValueError(f"Unsupported local artifact URI scheme: {parsed.scheme}")
        return Path(uri)
