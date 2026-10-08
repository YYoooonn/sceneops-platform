"""I-36: the acquisition tool depends on no SceneOps package.

Checked three ways: no source or test file imports a SceneOps module, the
project declares and locks no SceneOps distribution, and the tool's own
environment cannot import one at all.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SELF = "sceneops-dataset-acquisition"
FORBIDDEN_MODULES = ("sceneops_", "sceneops")


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_no_file_imports_a_sceneops_module() -> None:
    offenders = {
        str(path.relative_to(PROJECT)): sorted(
            m
            for m in _imported_modules(path)
            if m.split(".")[0].startswith(FORBIDDEN_MODULES)
        )
        for path in [*PROJECT.glob("src/**/*.py"), *PROJECT.glob("tests/**/*.py")]
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_project_declares_and_locks_no_sceneops_distribution() -> None:
    pyproject = tomllib.loads((PROJECT / "pyproject.toml").read_text())
    declared = [
        *pyproject["project"]["dependencies"],
        *(
            d
            for group in pyproject.get("dependency-groups", {}).values()
            for d in group
        ),
    ]
    assert not [d for d in declared if d.lower().startswith("sceneops")]
    assert "sources" not in pyproject.get("tool", {}).get("uv", {})

    lock = tomllib.loads((PROJECT / "uv.lock").read_text())
    locked = {package["name"] for package in lock["package"]}
    assert {name for name in locked if name.startswith("sceneops")} == {SELF}
    self_entry = next(p for p in lock["package"] if p["name"] == SELF)
    assert self_entry["source"] == {"editable": "."}


def test_tool_environment_cannot_import_sceneops() -> None:
    probe = (
        "import importlib.util, sys\n"
        "import dataset_acquisition.cli, dataset_acquisition.nuscenes\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('sceneops'))\n"
        "available = [m for m in ('sceneops_core', 'sceneops_db', 'sceneops_storage',\n"
        "             'sceneops_streaming', 'sceneops_recording', 'sceneops_worker',\n"
        "             'sceneops_api') if importlib.util.find_spec(m)]\n"
        "assert not loaded and not available, (loaded, available)\n"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)
