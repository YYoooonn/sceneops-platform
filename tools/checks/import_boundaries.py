#!/usr/bin/env python
"""Dependency direction of the monorepo (`make check-boundaries`).

The repository has four top-level categories (docs/architecture/repository-structure.md):

  apps/          canonical SceneOps runtime processes
  packages/      reusable production code, named by capability
  integrations/  adapters between SceneOps and an external protocol, runtime or data source
  tools/         developer / CI / benchmark / fixture utilities; never production

The rules, checked on the import statements of production code (every module under
`apps/*/<module>`, `packages/*/<module>` and `integrations/*/<module>`; tests are not
production code):

  apps          -> packages   an app imports packages, never another app, an integration
                              or tools
  integrations  -> packages   an integration imports the packages it is declared to use
                              (possibly none), never an app, another integration or tools
  packages      -> packages   along the declared layering below, never an app, an
                              integration or tools
  tools         -> packages   nothing production imports tools

and, for every app, package and integration:

  * it imports only the workspace packages it is declared to depend on here;
  * every workspace package it imports is also declared in its own pyproject.toml, so a
    container image built from that pyproject contains what the code needs;
  * it declares no dependency on an app or an integration (an integration is reached over
    its external protocol, never as a Python distribution);
  * its Dockerfile copies no `tools/` path and no source of another category.

Compose build contexts and Dockerfiles must exist, so no service depends on a deleted
source path.

`PACKAGES` is the package layering; a new package or a new edge is a decision, taken by
editing this file. It prints one line per violation and exits non-zero when there is any.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from dataclasses import dataclass
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
    "sceneops_publisher": {"sceneops_core", "sceneops_storage", "sceneops_recording"},
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

KINDS = {"apps": "app", "packages": "package", "integrations": "integration"}

# integration directory -> workspace packages it may import. Keyed by directory, not by
# import name: an integration may declare no importable module (lerobot runs a package
# entrypoint inside its own locked environment), and the directory is its identity.
INTEGRATIONS: dict[str, set[str]] = {
    # ROS 2 topics -> TelemetryEnvelope -> Kafka: shares the transport contract with Capture.
    "ros2-kafka-bridge": {"sceneops_core", "sceneops_streaming"},
    # GroundingDINO serving runtime: reached by sceneops_inference over HTTP only.
    "groundingdino-server": set(),
    # External dataset -> MCAP, and MCAP -> ROS 2 replay: depends on no SceneOps package.
    "dataset-acquisition": set(),
    # LeRobot export runtime: executes sceneops_analytics' adapter in its own environment.
    "lerobot": {"sceneops_core", "sceneops_storage", "sceneops_analytics"},
}


@dataclass(frozen=True)
class Project:
    kind: str  # app | package | integration
    key: str  # import name (app, package) or directory name (integration)
    path: Path
    dist: str  # distribution name from [project].name
    modules: dict[str, Path]  # import name -> source directory


def _projects(root: Path) -> list[Project]:
    projects: list[Project] = []
    for category, kind in KINDS.items():
        base = root / category
        if not base.is_dir():
            continue
        for path in sorted(
            p for p in base.iterdir() if (p / "pyproject.toml").is_file()
        ):
            config = tomllib.loads((path / "pyproject.toml").read_text())
            wheel = (
                config.get("tool", {})
                .get("hatch", {})
                .get("build", {})
                .get("targets", {})
                .get("wheel", {})
                .get("packages", [])
            )
            modules = {Path(w).name: path / w for w in wheel}
            keys = [path.name] if kind == "integration" else list(modules)
            dist = config.get("project", {}).get("name", path.name)
            projects.extend(Project(kind, key, path, dist, modules) for key in keys)
    return projects


def _imports(path: Path) -> set[str]:
    """Absolute dotted module names imported anywhere in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _declared_dists(project: Path) -> set[str]:
    config = tomllib.loads((project / "pyproject.toml").read_text())
    dependencies = list(config.get("project", {}).get("dependencies", []))
    for extra in config.get("project", {}).get("optional-dependencies", {}).values():
        dependencies.extend(extra)
    names = set()
    for requirement in dependencies:
        match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        if match:
            names.add(match.group(0).lower().replace("_", "-"))
    return names


def _dockerfile_problems(root: Path, project: Project) -> list[str]:
    dockerfile = project.path / "Dockerfile"
    if not dockerfile.is_file():
        return []
    own = project.path.relative_to(root).as_posix()
    # An image copies `packages/` and its own directory, never tools/ or any other
    # app or integration.
    pattern = re.compile(r"(?<![\w-])(tools|apps|integrations)/")
    problems = []
    for number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
        if not re.match(r"\s*(COPY|ADD)\b", line):
            continue
        hit = pattern.search(line.replace(own, ""))
        if hit:
            problems.append(
                f"{dockerfile.relative_to(root)}:{number}: the image copies "
                f"`{hit.group(0)}` (another category)"
            )
    return problems


def _compose_problems(root: Path) -> list[str]:
    """Every build context and Dockerfile named by a Compose file exists."""
    problems = []
    for compose in sorted((root / "compose").glob("*.yaml")):
        context = Path(".")
        for number, raw in enumerate(compose.read_text().splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            where = f"{compose.relative_to(root)}:{number}"
            if line.startswith("build:"):
                context = Path(".")
            elif match := re.match(r"context:\s*(\S+)", line):
                context = Path(match.group(1))
                if not (root / context).is_dir():
                    problems.append(
                        f"{where}: build context `{context}` does not exist"
                    )
            elif match := re.match(r"dockerfile:\s*(\S+)", line):
                if not (root / context / match.group(1)).is_file():
                    problems.append(
                        f"{where}: Dockerfile `{match.group(1)}` does not exist "
                        f"in context `{context}`"
                    )
    return problems


def check(root: Path = REPO_ROOT) -> list[str]:
    problems: list[str] = []
    projects = _projects(root)
    tables = {"package": PACKAGES, "app": APPS, "integration": INTEGRATIONS}

    package_modules = {p.key for p in projects if p.kind == "package"}
    app_modules = {p.key for p in projects if p.kind == "app"}
    # Import names of integration source: nothing else may import them.
    integration_modules = {
        module for p in projects if p.kind == "integration" for module in p.modules
    }
    by_dist = {p.dist.lower().replace("_", "-"): p for p in projects}

    def module_of(dist: str) -> str:
        known = by_dist.get(dist)
        return (
            known.key
            if known and known.kind != "integration"
            else dist.replace("-", "_")
        )

    for category in KINDS:
        base = root / category
        for stray in sorted(base.iterdir()) if base.is_dir() else []:
            if (
                stray.is_dir()
                and not stray.name.startswith((".", "__"))
                and not (stray / "pyproject.toml").is_file()
            ):
                problems.append(
                    f"{stray.relative_to(root)}: not a project (no pyproject.toml)"
                )

    for project in projects:
        allowed = tables[project.kind].get(project.key)
        pyproject = (project.path / "pyproject.toml").relative_to(root)
        if allowed is None:
            problems.append(
                f"{pyproject.parent}: `{project.key}` is not declared in "
                "tools/checks/import_boundaries.py"
            )
            continue
        used: set[str] = set()
        for module, source in project.modules.items():
            for file in sorted(source.rglob("*.py")):
                relative = file.relative_to(source)
                scope = relative.parts[0] if len(relative.parts) > 1 else ""
                extra = SCOPED.get((module, scope), set())
                where = f"{file.relative_to(root)}"
                for name in _imports(file):
                    top = name.split(".")[0]
                    if top == "tools":
                        problems.append(
                            f"{where}: production code imports `{name}` (tools)"
                        )
                    elif top == "tests":
                        problems.append(
                            f"{where}: production code imports `{name}` (tests)"
                        )
                    elif top in project.modules:
                        continue
                    elif top in app_modules:
                        problems.append(
                            f"{where}: imports the app `{top}`; apps are never imported"
                        )
                    elif top in integration_modules:
                        problems.append(
                            f"{where}: imports the integration `{top}`; integrations "
                            "are reached over their external protocol, never imported"
                        )
                    elif top in package_modules:
                        used.add(top)
                        if top not in allowed or (
                            top in SCOPE_ONLY.get(module, set()) and top not in extra
                        ):
                            problems.append(
                                f"{where}: `{project.key}` may not import `{top}` "
                                f"({project.kind} layering)"
                            )

        declared = _declared_dists(project.path)
        declared_modules = {module_of(d) for d in declared}
        for top in sorted(used - declared_modules - set(project.modules)):
            problems.append(
                f"{pyproject}: imports `{top}` but does not declare it as a dependency"
            )
        for dist in sorted(declared):
            other = by_dist.get(dist)
            if other and other.path != project.path and other.kind != "package":
                problems.append(
                    f"{pyproject}: declares a dependency on the {other.kind} `{dist}`; "
                    f"{other.kind}s are never dependencies"
                )
        if project.kind == "integration":
            for top in sorted((declared_modules & package_modules) - allowed):
                problems.append(
                    f"{pyproject}: integration `{project.key}` may not depend on `{top}`"
                )

        problems.extend(_dockerfile_problems(root, project))

    seen = {(p.kind, p.key) for p in projects}
    for kind, table in tables.items():
        for key in sorted(table):
            if (kind, key) not in seen:
                problems.append(
                    f"{kind} `{key}` is declared in import_boundaries.py but does not exist"
                )

    problems.extend(_compose_problems(root))

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
            f"✅ import boundaries hold: {len(APPS)} apps, {len(PACKAGES)} packages, "
            f"{len(INTEGRATIONS)} integrations; apps/integrations -> packages, packages "
            "↛ apps/integrations/tools, apps ↛ apps/integrations, integrations ↛ "
            "apps/integrations, no cycles"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
