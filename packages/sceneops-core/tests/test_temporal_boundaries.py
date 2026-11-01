"""Dependency direction of the temporal package (ADR-015 §6.2): time semantics
depend on no source, binding or domain module; source bindings and canonical
schemas depend on them.

    sceneops_core.temporal    clocks, origins, comparability, frame grid,
                              source-time values
    sceneops_core.provenance  source bindings: assert a unit's origin scope and
                              permitted origins in temporal terms

Static checks read the import statements; runtime checks import a module in
a fresh interpreter and list every ``sceneops_core`` module that loading it
initialized, so a package ``__init__`` re-export counts as well."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import sceneops_core
import sceneops_core.provenance as provenance
import sceneops_core.temporal as temporal
from sceneops_core.artifacts.schemas import payload as payload_module
from sceneops_core.provenance import sources as sources_module

CORE_ROOT = Path(sceneops_core.__file__).parent
TEMPORAL_ROOT = CORE_ROOT / "temporal"
# Subpackages that know a source, a binding or a canonical domain.
SOURCE_OR_DOMAIN = ("provenance", "sources", "robots", "scenes", "episodes")


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


def _loaded_by(module: str) -> list[str]:
    """``sceneops_core`` modules initialized by importing ``module`` in a
    fresh interpreter."""
    code = (
        "import importlib, json, sys\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('sceneops_core'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def _subpackages(modules: list[str]) -> set[str]:
    return {m.split(".")[1] for m in modules if m.count(".") >= 1}


# --- static ------------------------------------------------------------------------


def test_temporal_modules_import_only_generic_platform_primitives():
    for path in sorted(TEMPORAL_ROOT.glob("*.py")):
        for module in _platform_imports(path):
            assert module.startswith(
                ("sceneops_core.temporal", "sceneops_core.common")
            ), f"{path.name} imports {module}"


def test_payload_ref_takes_its_timestamp_type_from_temporal():
    imports = _platform_imports(Path(payload_module.__file__))
    assert "sceneops_core.temporal" in imports
    assert not [m for m in imports if m.startswith("sceneops_core.provenance")]


def test_source_bindings_depend_on_temporal_not_the_reverse():
    assert "sceneops_core.temporal" in _platform_imports(Path(sources_module.__file__))


@pytest.mark.parametrize("module", ["clocks", "frame_time", "source_time"])
def test_provenance_neither_holds_nor_reexports_temporal_modules(module):
    assert importlib.util.find_spec(f"sceneops_core.provenance.{module}") is None
    assert not set(temporal.__all__) & set(dir(provenance))


def test_recording_clock_policy_stays_with_the_recording_binding():
    """Which clocks a recording declares, and with which origin, is the
    recording source's statement; temporal holds no source policy."""
    assert provenance.recording_clock_domains.__module__ == sources_module.__name__
    assert not hasattr(temporal, "recording_clock_domains")
    assert not hasattr(temporal, "RECORDING_CLOCK_ORIGIN")


# --- runtime -----------------------------------------------------------------------


def test_importing_temporal_initializes_no_source_or_domain_module():
    loaded = _loaded_by("sceneops_core.temporal")
    assert not _subpackages(loaded) & set(SOURCE_OR_DOMAIN), loaded


def test_importing_payload_ref_initializes_no_source_or_domain_module():
    loaded = _loaded_by("sceneops_core.artifacts.schemas.payload")
    assert "sceneops_core.temporal.source_time" in loaded
    assert not _subpackages(loaded) & set(SOURCE_OR_DOMAIN), loaded
