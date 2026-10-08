from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from sceneops_recording.testing.captures import (
    DEFAULT_MESSAGES,
    MessageSpec,
    build_mcap,
    write_finalized_capture,
)


@pytest.fixture()
def write_mcap(tmp_path: Path) -> Callable[..., Path]:
    def _write(
        messages: Iterable[MessageSpec] = DEFAULT_MESSAGES,
        *,
        name: str = "run.mcap",
        library: str = "test",
    ) -> Path:
        path = tmp_path / "captures" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_mcap(messages, library=library))
        return path

    return _write


@pytest.fixture()
def make_capture() -> Callable[..., Path]:
    """A finalized capture directory the way Capture leaves it."""
    return write_finalized_capture
