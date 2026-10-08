"""The dependency-direction check rejects what it claims to reject, and the repository
satisfies it."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "import_boundaries", REPO_ROOT / "tools" / "checks" / "import_boundaries.py"
)
assert _SPEC and _SPEC.loader
boundaries = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = boundaries
_SPEC.loader.exec_module(boundaries)


def _project(
    root: Path,
    kind: str,
    name: str,
    module: str,
    depends: list[str],
    files: dict[str, str],
) -> None:
    project = root / kind / name
    (project / module).mkdir(parents=True)
    deps = ", ".join(f'"{d}"' for d in depends)
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = [{deps}]\n'
        f'[tool.hatch.build.targets.wheel]\npackages = ["{module}"]\n'
    )
    (project / module / "__init__.py").write_text("")
    for relative, content in files.items():
        target = project / module / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _repo(tmp_path: Path, **overrides: dict) -> Path:
    """A minimal valid tree: every declared app and package, importing nothing."""
    for module in boundaries.PACKAGES:
        spec = overrides.get(module, {})
        _project(
            tmp_path,
            "packages",
            module.replace("_", "-"),
            module,
            spec.get("depends", []),
            spec.get("files", {}),
        )
    for module in boundaries.APPS:
        spec = overrides.get(module, {})
        _project(
            tmp_path,
            "apps",
            module.replace("_", "-"),
            module,
            spec.get("depends", []),
            spec.get("files", {}),
        )
    return tmp_path


def test_a_tree_without_violations_passes(tmp_path):
    assert boundaries.check(_repo(tmp_path)) == []


def test_a_package_importing_an_app_is_rejected(tmp_path):
    root = _repo(
        tmp_path,
        sceneops_recording={"files": {"x.py": "import sceneops_publisher\n"}},
    )
    assert any(
        "imports the app `sceneops_publisher`" in p for p in boundaries.check(root)
    )


def test_production_code_importing_tools_is_rejected(tmp_path):
    root = _repo(
        tmp_path, sceneops_core={"files": {"x.py": "from tools.checks import y\n"}}
    )
    assert any("(tools)" in p for p in boundaries.check(root))


def test_an_app_importing_another_app_is_rejected(tmp_path):
    root = _repo(
        tmp_path, sceneops_capture={"files": {"x.py": "import sceneops_worker\n"}}
    )
    assert any(
        "sceneops_capture" in p and "sceneops_worker" in p
        for p in boundaries.check(root)
    )


def test_a_package_importing_above_its_layer_is_rejected(tmp_path):
    root = _repo(
        tmp_path,
        sceneops_core={
            "files": {"x.py": "from sceneops_db import session\n"},
            "depends": ["sceneops-db"],
        },
    )
    assert any(
        "`sceneops_core` may not import `sceneops_db`" in p
        for p in boundaries.check(root)
    )


def test_scene_and_episode_stay_siblings(tmp_path):
    root = _repo(
        tmp_path,
        sceneops_episodes={
            "files": {"x.py": "import sceneops_scenes\n"},
            "depends": ["sceneops-scenes"],
        },
    )
    assert any(
        "`sceneops_episodes` may not import `sceneops_scenes`" in p
        for p in boundaries.check(root)
    )


def test_recording_reaches_the_streaming_transport_only_from_capture(tmp_path):
    ok = _repo(
        tmp_path / "ok",
        sceneops_recording={
            "files": {"capture/consumer.py": "import sceneops_streaming\n"},
            "depends": ["sceneops-streaming"],
        },
    )
    assert boundaries.check(ok) == []
    bad = _repo(
        tmp_path / "bad",
        sceneops_recording={
            "files": {"publisher.py": "import sceneops_streaming\n"},
            "depends": ["sceneops-streaming"],
        },
    )
    assert any(
        "`sceneops_recording` may not import `sceneops_streaming`" in p
        for p in boundaries.check(bad)
    )


def test_an_import_missing_from_the_pyproject_is_rejected(tmp_path):
    root = _repo(
        tmp_path, sceneops_storage={"files": {"x.py": "import sceneops_core\n"}}
    )
    assert any("does not declare" in p for p in boundaries.check(root))


def test_an_image_that_copies_tools_is_rejected(tmp_path):
    root = _repo(tmp_path)
    (root / "apps" / "sceneops-capture" / "Dockerfile").write_text(
        "COPY tools/e2e /x\n"
    )
    assert any("copies tools/" in p for p in boundaries.check(root))


def test_the_repository_satisfies_the_boundaries():
    assert boundaries.check(REPO_ROOT) == []
