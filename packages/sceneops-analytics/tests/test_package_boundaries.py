"""Regression guard for SceneOps V2 Request 3.2C.1 §1: sceneops-analytics
is a production package (the columnar analytics export layer) and must
never depend on sceneops-db -- that dependency existed for exactly one
reason (the E2E fixture bootstrap's Postgres repository usage), and that
code now lives in scripts/e2e/e2e_fixture_bootstrap.py instead.

Importing sceneops_analytics (including its sceneops_analytics.testing
submodule, which is still test-support code but must stay DB-free) must
work with sceneops_db absent from sys.modules and without ever importing
it as a side effect.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import sceneops_analytics

_PACKAGE_ROOT = Path(sceneops_analytics.__file__).resolve().parent


def _iter_source_files():
    yield from _PACKAGE_ROOT.rglob("*.py")


def test_no_source_file_imports_sceneops_db():
    offenders = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            if any(name and name.split(".")[0] == "sceneops_db" for name in names):
                offenders.append(str(path.relative_to(_PACKAGE_ROOT)))

    assert offenders == [], (
        "sceneops-analytics must not depend on sceneops-db (SceneOps V2 "
        f"Request 3.2C.1 §1); found imports in: {offenders}"
    )


def test_importing_sceneops_analytics_does_not_pull_in_sceneops_db():
    was_loaded_before = "sceneops_db" in sys.modules
    for module_name in list(sys.modules):
        if module_name == "sceneops_db" or module_name.startswith("sceneops_db."):
            del sys.modules[module_name]

    importlib.reload(sceneops_analytics)
    importlib.import_module("sceneops_analytics.testing")

    newly_loaded = "sceneops_db" in sys.modules
    assert not newly_loaded, (
        "importing sceneops_analytics (including .testing) must never "
        "import sceneops_db as a side effect"
    )

    if was_loaded_before:
        importlib.import_module("sceneops_db")
