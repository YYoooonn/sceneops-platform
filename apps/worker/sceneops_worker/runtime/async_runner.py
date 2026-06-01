from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class AsyncRuntimeRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def shutdown(self) -> None:
        with self._lock:
            if self._loop is None:
                return

            loop = self._loop
            thread = self._thread

            if loop.is_running():
                shutdown_future = asyncio.run_coroutine_threadsafe(
                    _shutdown_runtime_resources(),
                    loop,
                )
                shutdown_future.result(timeout=10)

                loop.call_soon_threadsafe(loop.stop)

            if thread is not None:
                thread.join(timeout=10)

            self._loop = None
            self._thread = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop

            loop = asyncio.new_event_loop()

            thread = threading.Thread(
                target=self._run_loop,
                args=(loop,),
                name="sceneops-worker-async-loop",
                daemon=True,
            )
            thread.start()

            self._loop = loop
            self._thread = thread

            return loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)

        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)

            for task in pending:
                task.cancel()

            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()


async def _shutdown_runtime_resources() -> None:
    from sceneops_db.session import dispose_async_engine

    await dispose_async_engine()


_async_runtime_runner: AsyncRuntimeRunner | None = None


def get_async_runtime_runner() -> AsyncRuntimeRunner:
    global _async_runtime_runner

    if _async_runtime_runner is None:
        _async_runtime_runner = AsyncRuntimeRunner()

    return _async_runtime_runner


def shutdown_async_runtime_runner() -> None:
    global _async_runtime_runner

    if _async_runtime_runner is None:
        return

    _async_runtime_runner.shutdown()
    _async_runtime_runner = None
