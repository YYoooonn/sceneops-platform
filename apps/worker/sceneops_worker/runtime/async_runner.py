from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class AsyncRuntimeRunner:
    @staticmethod
    def run(coro: Coroutine[Any, Any, T]) -> T:
        result: list[T] = []
        exc: list[BaseException] = []

        def _target() -> None:
            try:
                result.append(asyncio.run(coro))
            except BaseException as e:
                exc.append(e)

        thread = threading.Thread(
            target=_target,
            name="sceneops-worker-async-run",
            daemon=True,
        )
        thread.start()
        thread.join()

        if exc:
            raise exc[0]

        return result[0]


def shutdown_async_runtime_runner() -> None:
    """No-op — runner no longer holds a persistent thread."""
