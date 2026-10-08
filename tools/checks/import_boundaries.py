#!/usr/bin/env python
"""Dependency direction of the monorepo (`make check-boundaries`).

The rules, checked on the import statements of production code (every module under
`apps/*/<module>` and `packages/*/<module>`; tests are not production code):

  apps      -> packages        an app imports packages, never another app, never tools
  packages  -> packages        along the declared layering below, never an app, never tools
  tools     -> packages        tools may import anything production; nothing imports tools

and, for every app and package:

  * it imports only the workspace packages it is declared to depend on here;
  * every workspace package it imports is also declared in its own pyproject.toml, so a
    container image built from that pyproject contains what the code needs;
  * the Dockerfile of an app or package copies no `tools/` path into the image.

`PACKAGES` is the package layering; a new package or a new edge is a decision, taken by
editing this file. It prints one line per violation and exits non-zero when there is any.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# import name -> workspace packages it may import (besides itself).
PACKAGES: dict[str, set[str]] = {
    # The contracts every other package shares: IDs, enums, domain schemas, pure helpers.
    "sceneops_core": set(),
    # Infrastructure adapters.
    "sceneops_storage": {"sceneops_core"},
    "sceneops_db": {"sceneops_core"},
    "sceneops_streaming": {"sceneops_core"},
    # Recording: capture (Kafka -> MCAP) and publication of finalized recordings.
    # `sceneops_streaming` is reached only from the `capture` subpackage.
    "sceneops_recording": {"sceneops_core", "sceneops_storage", "sceneops_streaming"},
    # Execution of durable Jobs and Pipelines over PostgreSQL.
    "sceneops_execution": {"sceneops_core", "sceneops_db"},
    # Acquisition lifecycle: reconciliation, status, artifact lifecycle.
    "sceneops_acquisition": {
        "sceneops_core",
        "sceneops_db",
        "sceneops_storage",
        "sceneops_recording",
        "sceneops_execution",
    },
    # Derived-layer storage and the capabilities built on it. Scene and Episode are
    # siblings: neither imports the other.
    "sceneops_derived": {"sceneops_core", "sceneops_storage"},
    "sceneops_scenes": {"sceneops_core", "sceneops_storage", "sceneops_recording"},
    "sceneops_episodes": {"sceneops_core", "sceneops_storage", "sceneops_recording"},
    "sceneops_inference": {"sceneops_core", "sceneops_storage", "sceneops_derived"},
    "sceneops_evaluation": {"sceneops_core", "sceneops_derived"},
    "sceneops_analytics": {"sceneops_core", "sceneops_storage"},
}

# import name -> workspace packages the app may import. The import name of an app is the
# top-level module of its source directory.
APPS: dict[str, set[str]] = {
    "app": {
        "sceneops_core",
        "sceneops_db",
        "sceneops_storage",
        "sceneops_execution",
        "sceneops_acquisition",
    },
    "sceneops_worker": {
        "sceneops_core",
        "sceneops_db",
        "sceneops_storage",
        "sceneops_execution",
        "sceneops_acquisition",
        "sceneops_recording",
        "sceneops_analytics",
        "sceneops_scenes",
        "sceneops_episodes",
        "sceneops_derived",
        "sceneops_inference",
        "sceneops_evaluation",
    },
    "sceneops_capture": {"sceneops_core", "sceneops_streaming", "sceneops_recording"},
    "sceneops_streaming_bridge": {"sceneops_core", "sceneops_streaming"},
    "sceneops_publisher": {"sceneops_core", "sceneops_storage", "sceneops_recording"},
    "inference_server": set(),
}

# (module prefix of a package) -> workspace packages that module alone may import on top
# of the package's own allowance: sceneops_recording reaches the streaming transport only
# from its `capture` subpackage.
SCOPED: dict[tuple[str, str], set[str]] = {
    ("sceneops_recording", "capture"): {"sceneops_streaming"},
}
# Packages that are only reachable from a subpackage (see SCOPED): outside it they are
# forbidden even though the package allowance lists them.
SCOPE_ONLY: dict[str, set[str]] = {"sceneops_recording": {"sceneops_streaming"}}

WORKSPACE = set(PACKAGES) | set(APPS)
FORBIDDEN_TARGETS = {"tools"}


def _dist_to_module(name: str) -> str:
    if name == "sceneops-api":
        return "app"
    if name == "sceneops-inference-server":
        return "inference_server"
    return name.replace("-", "_")


def _units(root: Path) -> dict[str, tuple[str, Path, Path]]:
    """import name -> (kind, source dir, project dir) for every app and package."""
    units: dict[str, tuple[str, Path, Path]] = {}
    for kind, base in (("app", root / "apps"), ("package", root / "packages")):
        if not base.is_dir():
            continue
        for project in sorted(
            p for p in base.iterdir() if (p / "pyproject.toml").is_file()
        ):
            config = tomllib.loads((project / "pyproject.toml").read_text())
            wheel = (
                config.get("tool", {})
                .get("hatch", {})
                .get("build", {})
                .get("targets", {})
                .get("wheel", {})
                .get("packages", [])
            )
            for module in wheel:
                units[module] = (kind, project / module, project)
    return units


def _imports(path: Path) -> set[str]:
    """Absolute dotted module names imported anywhere in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _declared(project: Path) -> set[str]:
    config = tomllib.loads((project / "pyproject.toml").read_text())
    dependencies = list(config.get("project", {}).get("dependencies", []))
    for extra in config.get("project", {}).get("optional-dependencies", {}).values():
        dependencies.extend(extra)
    names = set()
    for requirement in dependencies:
        match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        if match:
            names.add(_dist_to_module(match.group(0).lower()))
    return names


def check(root: Path = REPO_ROOT) -> list[str]:
    problems: list[str] = []
    units = _units(root)

    for module, (kind, source, project) in sorted(units.items()):
        table = PACKAGES if kind == "package" else APPS
        if module not in table:
            problems.append(
                f"{project.relative_to(root)}: `{module}` is not declared in "
                "tools/checks/import_boundaries.py"
            )
            continue
        allowed = table[module]
        used: set[str] = set()
        for file in sorted(source.rglob("*.py")):
            relative = file.relative_to(source)
            scope = relative.parts[0] if len(relative.parts) > 1 else ""
            extra = SCOPED.get((module, scope), set())
            for name in _imports(file):
                top = name.split(".")[0]
                where = f"{file.relative_to(root)}"
                if top in FORBIDDEN_TARGETS:
                    problems.append(
                        f"{where}: production code imports `{name}` (tools)"
                    )
                    continue
                if top == module or top not in WORKSPACE:
                    if top == "tests":
                        problems.append(
                            f"{where}: production code imports `{name}` (tests)"
                        )
                    continue
                used.add(top)
                if top in APPS:
                    problems.append(
                        f"{where}: imports the app `{top}`; apps are never imported"
                    )
                elif top not in allowed or (
                    top in SCOPE_ONLY.get(module, set()) and top not in extra
                ):
                    problems.append(
                        f"{where}: `{module}` may not import `{top}` ({kind} layering)"
                    )
        missing = used - _declared(project) - {module}
        for top in sorted(missing):
            problems.append(
                f"{(project / 'pyproject.toml').relative_to(root)}: imports `{top}` but does "
                "not declare it as a dependency"
            )
        dockerfile = project / "Dockerfile"
        if dockerfile.is_file():
            for number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
                if re.match(r"\s*(COPY|ADD)\b.*\btools/", line):
                    problems.append(
                        f"{dockerfile.relative_to(root)}:{number}: the image copies tools/"
                    )

    for module in sorted(set(PACKAGES) | set(APPS)):
        if module not in units:
            problems.append(
                f"`{module}` is declared in import_boundaries.py but does not exist"
            )

    # Acyclic package layering (the declared table itself).
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if node in done:
            return
        if node in visiting:
            problems.append(
                "package layering has a cycle: " + " -> ".join((*trail, node))
            )
            return
        visiting.add(node)
        for target in sorted(PACKAGES.get(node, ())):
            visit(target, (*trail, node))
        visiting.discard(node)
        done.add(node)

    for node in sorted(PACKAGES):
        visit(node, ())
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(f"❌ {problem}")
    if not problems:
        print(
            f"✅ import boundaries hold: {len(APPS)} apps, {len(PACKAGES)} packages; "
            "apps -> packages, packages ↛ apps/tools, no cycles"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
