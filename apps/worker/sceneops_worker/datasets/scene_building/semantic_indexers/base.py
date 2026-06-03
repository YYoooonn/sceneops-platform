from __future__ import annotations

from typing import Protocol

from sceneops_worker.datasets.scene_building.semantic_models import IndexedKeyframe


class SemanticLogIndexer(Protocol):
    async def index(self) -> list[IndexedKeyframe]: ...
