from __future__ import annotations

from typing import Protocol

from sceneops_worker.datasets.scene_building.models import IndexedRawFrame


class RawLogIndexer(Protocol):
    async def index(self) -> list[IndexedRawFrame]: ...
