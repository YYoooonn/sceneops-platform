"""Real files on disk and a real LocalArtifactStore under tmp_path."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from sceneops_storage import LocalArtifactStore
from tests.tiny_source import TREE, write_tree


@pytest.fixture()
def make_tree(tmp_path: Path) -> Callable[..., Path]:
    def _make(files: dict[str, bytes] = TREE, name: str = "source") -> Path:
        return write_tree(tmp_path / name, files)

    return _make


@pytest.fixture()
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(root_uri=str(tmp_path / "store"))


@pytest.fixture()
def sources_root(tmp_path: Path) -> str:
    return str(tmp_path / "store" / "sources")
