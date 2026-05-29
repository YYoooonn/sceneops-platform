from __future__ import annotations

from typing import Any, Protocol


class ArtifactStore(Protocol):
    def join_uri(self, root: str, *parts: str) -> str:
        raise NotImplementedError

    async def exists(self, uri: str) -> bool:
        raise NotImplementedError

    async def read_json(self, uri: str) -> Any:
        raise NotImplementedError

    async def write_json(
        self,
        uri: str,
        payload: Any,
    ) -> None:
        raise NotImplementedError

    async def list_json(
        self,
        uri: str,
    ) -> list[str]:
        raise NotImplementedError

    async def delete_prefix(
        self,
        uri: str,
    ) -> None:
        raise NotImplementedError

    def public_url(
        self,
        uri: str,
    ) -> str:
        raise NotImplementedError
