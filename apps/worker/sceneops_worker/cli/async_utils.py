from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sceneops_db.session import dispose_async_engine

T = TypeVar("T")


def run_cli_async(factory: Callable[[], Awaitable[T]]) -> T:
    """Run one async CLI command with explicit DB cleanup.

    CLI commands are short-lived processes, so asyncio.run() is fine.
    The important part is to dispose SQLAlchemy async engine before
    the event loop is closed.
    """

    async def _main() -> T:
        try:
            return await factory()
        finally:
            await dispose_async_engine()

    return asyncio.run(_main())
