"""Source preservation stays format- and SDK-free (ADR-015 §10, MS-13)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "sceneops_sources"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_FIRST_PARTY = {"sceneops_core", "sceneops_storage", "sceneops_sources"}


def _top_level_imports() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for file in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = {node.module.split(".")[0]}
            else:
                continue
            for name in names:
                found.setdefault(name, set()).add(file.name)
    return found


def test_imports_only_the_standard_library_core_and_storage():
    foreign = {
        name: files
        for name, files in _top_level_imports().items()
        if name not in sys.stdlib_module_names and name not in ALLOWED_FIRST_PARTY
    }
    assert foreign == {}


def test_names_no_dataset_format():
    """Preservation interprets nothing, so no format name appears in its code."""
    pattern = re.compile(r"nuscenes|lerobot|parquet|pyarrow|mcap", re.IGNORECASE)
    hits = [
        f"{file.name}:{number}"
        for file in sorted(PACKAGE.rglob("*.py"))
        for number, line in enumerate(file.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]
    assert hits == []


def test_the_workspace_lock_contains_no_lerobot():
    assert not re.search(
        r'^name = "lerobot"$', (REPO_ROOT / "uv.lock").read_text(), re.M
    )
