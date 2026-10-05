from __future__ import annotations

import json
import os
import shutil
import uuid
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


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        """Serialize fully in memory, then ``write_bytes`` (atomic): a
        serialization error writes nothing, and a crash never leaves
        truncated JSON at the key."""
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        await self.write_bytes(uri, data)

    async def read_bytes(self, uri: ArtifactUri) -> bytes:
        path = self._to_path(uri)
        if not path.exists():
            raise ArtifactNotFoundError(uri)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ArtifactReadError(f"Failed to read binary artifact: {uri}") from exc

    async def read_range(self, uri: ArtifactUri, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            raise ArtifactReadError(
                f"Invalid range for {uri}: offset={offset}, length={length}"
            )
        path = self._to_path(uri)
        if not path.exists():
            raise ArtifactNotFoundError(uri)
        try:
            with path.open("rb") as f:
                f.seek(offset)
                data = f.read(length)
        except OSError as exc:
            raise ArtifactReadError(
                f"Failed to read range from artifact: {uri}"
            ) from exc
        if len(data) != length:
            raise ArtifactReadError(
                f"Requested range [{offset}, {offset + length}) exceeds "
                f"artifact size for {uri} (got {len(data)} bytes)"
            )
        return data

    async def write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        """Atomic: the bytes are written and fsynced under a temporary name in
        the destination directory, then renamed over the final key, so a crash
        leaves either the previous object (or none) or the complete new one --
        never truncated bytes at a valid key. An interrupted write can leave a
        ``.<name>.<random>.tmp`` file behind; it never matches a final key.
        """
        path = self._to_path(uri)
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 0o666 & ~umask: the same mode Path.write_bytes produced.
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            _fsync_directory(path.parent)
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
