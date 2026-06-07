from __future__ import annotations

from sceneops_core.observations.schemas import RawLogSourceType

from .base import RawLogAdapter


class RawLogAdapterFactory:
    def __init__(self) -> None:
        self._adapters: dict[str, RawLogAdapter] = {}

    def register(
        self, source_type: RawLogSourceType | str, adapter: RawLogAdapter
    ) -> None:
        self._adapters[str(source_type)] = adapter

    def get(self, source_type: RawLogSourceType | str) -> RawLogAdapter:
        key = str(source_type)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise ValueError(f"No RawLogAdapter registered for source_type={key!r}")
        return adapter
