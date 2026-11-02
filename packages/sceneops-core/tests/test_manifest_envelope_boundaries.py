"""Dependency direction of the canonical manifest contracts (ADR-017):

    sceneops_core.common
        ↑
    sceneops_core.temporal                      clocks, origins, frame grid
        ↑
    sceneops_core.provenance                    source bindings, producer
        ↑
    sceneops_core.{scenes,episodes}.schemas     manifests (lineage = binding + producer),
                                                records, run records
        ↑
    {scenes,episodes}.{recording,lerobot}_build build configurations, builders, consumers

The manifest schemas name their source only through the binding they carry in
``lineage``; they depend on no build configuration, RobotRun, Source Registry or
other domain module. The Episode vocabulary and its build-configuration
independence are covered in detail by ``test_episode_vocabulary_boundaries.py``;
the temporal layer by ``test_temporal_boundaries.py``."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import sceneops_core

CORE_ROOT = Path(sceneops_core.__file__).parent
SCHEMA_PACKAGES = ("scenes", "episodes")
# What a manifest schema module may import from the platform, besides its own
# schema package.
ENVELOPE_DEPENDENCIES = (
    "sceneops_core.common",
    "sceneops_core.temporal",
    "sceneops_core.artifacts.schemas.payload",
    "sceneops_core.provenance",
    "sceneops_core.runs.schemas",
)


def _platform_imports(path: Path) -> list[str]:
    """Absolute ``sceneops*`` modules a file imports; relative imports are
    resolved against the file's package."""
    package = ".".join(path.relative_to(CORE_ROOT.parent).with_suffix("").parts[:-1])
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0]
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            found.append(module)
    return [m for m in found if m.startswith("sceneops")]


def _schema_modules() -> list[tuple[str, Path]]:
    return [
        (domain, path)
        for domain in SCHEMA_PACKAGES
        for path in sorted((CORE_ROOT / domain / "schemas").glob("*.py"))
    ]


@pytest.mark.parametrize(
    ("domain", "path"),
    _schema_modules(),
    ids=lambda v: v if isinstance(v, str) else f"{v.parent.parent.name}/{v.name}",
)
def test_manifest_schemas_import_only_the_envelope_layers(domain, path):
    own = f"sceneops_core.{domain}.schemas"
    for module in _platform_imports(path):
        assert module == own or module.startswith(
            (f"{own}.", *ENVELOPE_DEPENDENCIES)
        ), f"{domain}/schemas/{path.name} imports {module}"


def test_every_schema_package_is_checked():
    names = {(d, p.name) for d, p in _schema_modules()}
    assert ("scenes", "manifests.py") in names
    assert ("episodes", "manifests.py") in names


@pytest.mark.parametrize(
    ("schemas", "build_configs"),
    [
        (
            "sceneops_core.scenes.schemas",
            ("sceneops_core.scenes.recording_build",),
        ),
        (
            "sceneops_core.episodes.schemas",
            (
                "sceneops_core.episodes.recording_build",
                "sceneops_core.episodes.lerobot_build",
            ),
        ),
    ],
)
def test_loading_manifest_schemas_initializes_no_build_configuration(
    schemas, build_configs
):
    code = (
        "import importlib, json, sys\n"
        f"importlib.import_module({schemas!r})\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('sceneops_core'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    loaded = set(json.loads(result.stdout))
    assert schemas in loaded
    assert not loaded & set(build_configs), sorted(loaded & set(build_configs))
