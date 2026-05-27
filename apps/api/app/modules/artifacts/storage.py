from __future__ import annotations

from typing import Any, Protocol


class ArtifactStorage(Protocol):
    async def read_json(self, uri: str) -> Any: ...

    def public_url(self, uri: str) -> str: ...
