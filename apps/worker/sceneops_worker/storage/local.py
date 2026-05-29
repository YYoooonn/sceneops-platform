from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sceneops_worker.storage.artifacts import ArtifactStore


class LocalArtifactStore(ArtifactStore):
    def __init__(self, *, root_uri: str | None = None) -> None:
        self.root_uri = root_uri

    async def read_json(self, uri: str) -> Any:
        path = self._to_path(uri)

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    async def write_json(self, uri: str, data: Any) -> None:
        path = self._to_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")

    async def exists(self, uri: str) -> bool:
        return self._to_path(uri).exists()

    async def list_json(self, uri: str) -> list[str]:
        path = self._to_path(uri)

        if not path.exists():
            return []

        return [str(item) for item in sorted(path.glob("*.json")) if item.is_file()]

    async def delete_prefix(self, uri: str) -> None:
        path = self._to_path(uri)

        if path.exists():
            shutil.rmtree(path)

    def join_uri(self, *parts: str) -> str:
        if not parts:
            raise ValueError("At least one URI part is required")

        first = parts[0]

        if first.startswith("file://"):
            base = first.removeprefix("file://")
            return "file://" + str(Path(base, *parts[1:]))

        return str(Path(first, *parts[1:]))

    def _to_path(self, uri: str) -> Path:
        if uri.startswith("file://"):
            return Path(uri.removeprefix("file://"))

        path = Path(uri)

        if path.is_absolute() or self.root_uri is None:
            return path

        return self.root_uri / path
