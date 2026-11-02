"""Dependency direction of the shared Episode vocabulary: the stream roles
(``EpisodeStreamRole``) and the field path rule (``FieldPath``) belong to the
Episode semantic contract; every build configuration, builder and consumer
depends on them, and they depend on no source-specific configuration.

    sceneops_core.episodes.schemas   EpisodeManifest, records and the shared
                                     vocabulary (enums.py, fields.py)
    sceneops_core.episodes.{recording,lerobot}_build
                                     source-specific build configurations

Static checks read the import statements of every Python file in the
repository; runtime checks import a module in a fresh interpreter, so a
package ``__init__`` re-export counts as well."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import AfterValidator

import sceneops_core
from sceneops_core.episodes import schemas
from sceneops_core.episodes.schemas import EpisodeStreamRole, FieldPath
from sceneops_core.episodes.schemas import enums as enums_module
from sceneops_core.episodes.schemas import fields as fields_module

CORE_ROOT = Path(sceneops_core.__file__).parent
REPO_ROOT = CORE_ROOT.parents[2]
SCHEMAS_ROOT = CORE_ROOT / "episodes" / "schemas"
SCHEMAS_PACKAGE = "sceneops_core.episodes.schemas"
# Source-specific build configuration the Episode contract must not depend on.
BUILD_CONFIGS = (
    "sceneops_core.episodes.recording_build",
    "sceneops_core.episodes.lerobot_build",
)
VOCABULARY = frozenset({"EpisodeStreamRole", "FieldPath", "FIELD_PATH_PATTERN"})
SOURCE_DIRS = ("apps", "packages", "integrations", "tools", "tests")


def _module_name(path: Path) -> str | None:
    """Dotted module name of a file under a ``sceneops*`` top-level package."""
    parts = path.with_suffix("").parts
    for i, part in enumerate(parts):
        if part.startswith("sceneops") and part.isidentifier():
            return ".".join(parts[i:])
    return None


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    """``(module, imported names)`` of every import in a file; relative
    imports are resolved against the file's package."""
    module_name = _module_name(path) or ""
    package = module_name.rsplit(".", 1)[0]
    if path.name == "__init__.py":
        package = module_name.removesuffix(".__init__")
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0]
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            found.append((module, [alias.name for alias in node.names]))
    return found


def _repository_files() -> list[Path]:
    return sorted(
        path
        for top in SOURCE_DIRS
        for path in (REPO_ROOT / top).rglob("*.py")
        if ".venv" not in path.parts and "node_modules" not in path.parts
    )


def _loaded_by(module: str) -> list[str]:
    code = (
        "import importlib, json, sys\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('sceneops'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


# --- static ------------------------------------------------------------------------


def test_vocabulary_is_defined_in_the_episode_schema_package():
    assert EpisodeStreamRole.__module__ == enums_module.__name__
    (validator,) = [m for m in FieldPath.__metadata__ if isinstance(m, AfterValidator)]
    assert validator.func.__module__ == fields_module.__name__
    assert {"EpisodeStreamRole", "FieldPath", "FIELD_PATH_PATTERN"} <= set(
        schemas.__all__
    )


def test_vocabulary_has_one_definition_in_the_repository():
    definitions: list[str] = []
    for path in _repository_files():
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ClassDef) and node.name in VOCABULARY:
                definitions.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                definitions += [
                    f"{path.relative_to(REPO_ROOT)}:{t.id}"
                    for t in targets
                    if isinstance(t, ast.Name) and t.id in VOCABULARY
                ]
    assert sorted(definitions) == [
        "packages/sceneops-core/sceneops_core/episodes/schemas/enums.py:EpisodeStreamRole",
        "packages/sceneops-core/sceneops_core/episodes/schemas/fields.py:FIELD_PATH_PATTERN",
        "packages/sceneops-core/sceneops_core/episodes/schemas/fields.py:FieldPath",
    ]


def test_every_importer_takes_the_vocabulary_from_the_schema_package():
    stray = [
        f"{path.relative_to(REPO_ROOT)}: {name} from {module}"
        for path in _repository_files()
        for module, names in _imports(path)
        for name in names
        if name in VOCABULARY and not module.startswith(SCHEMAS_PACKAGE)
    ]
    assert not stray, stray


@pytest.mark.parametrize(
    "path", sorted(SCHEMAS_ROOT.glob("*.py")), ids=lambda path: path.name
)
def test_episode_schemas_import_no_build_configuration(path):
    for module, _ in _imports(path):
        assert not module.startswith(BUILD_CONFIGS), f"{path.name} imports {module}"


@pytest.mark.parametrize(
    "path",
    [Path(enums_module.__file__), Path(fields_module.__file__)],
    ids=lambda path: path.name,
)
def test_vocabulary_modules_import_no_platform_module(path):
    platform = [m for m, _ in _imports(path) if m.startswith("sceneops")]
    assert not platform, platform


@pytest.mark.parametrize(
    "relative",
    [
        "packages/sceneops-core/sceneops_core/episodes/recording_build.py",
        "packages/sceneops-core/sceneops_core/episodes/lerobot_build.py",
        "packages/sceneops-episodes/sceneops_episodes/recording_builder.py",
        "packages/sceneops-episodes/sceneops_episodes/lerobot_builder.py",
    ],
)
def test_builders_consume_the_shared_stream_roles(relative):
    imports = _imports(REPO_ROOT / relative)
    assert (SCHEMAS_PACKAGE, "EpisodeStreamRole") in {
        (module, name) for module, names in imports for name in names
    }


# --- runtime -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "sceneops_core.episodes.schemas.enums",
        "sceneops_core.episodes.schemas.fields",
        "sceneops_core.episodes.schemas.manifests",
        "sceneops_core.episodes.schemas",
        "sceneops_core.episodes",
        "sceneops_core.episodes.lerobot_build",
        "sceneops_core.episodes.alignment",
    ],
)
def test_generic_episode_modules_do_not_initialize_the_recording_build(module):
    loaded = _loaded_by(module)
    assert "sceneops_core.episodes.recording_build" not in loaded
    assert "sceneops_core.robots.recording_payload" not in loaded
