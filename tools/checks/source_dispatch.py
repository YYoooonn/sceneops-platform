#!/usr/bin/env python
"""Source-kind dispatch stays where ADR-015 puts it (MS-10, MS-13; `make test`).

Downstream of canonical manifests no workflow branches on the source kind or the
source format. The one dispatch is payload resolution (the payload locator), which
branches on the payload reference type; a consumer that cannot represent a content
payload refuses it by capability. Upstream of the manifests (source preservation,
the format builders, canonical registration and the contracts that define a binding)
dispatch by source is the point.

Checked statically on production code (`apps/*/<module>`, `packages/*/<module>`;
tests, `testing.py` and synthetic-tree helpers are not production code). A *dispatch
site* is any of:

  * a comparison that reads a `source_kind` or `format` name or attribute, or compares
    with a source-kind literal ("recording", "content"), a payload-kind literal
    ("artifact", "source_file") next to a `kind`, or a format literal ("nuscenes",
    "lerobot");
  * an `isinstance` / `issubclass` test, or a `match` class pattern, on a source
    binding, source revision, pinned source or payload reference class;
  * a dict literal keyed by a format literal (a format registry).

Every site must lie in an UPSTREAM module or be one of the DOWNSTREAM sites below,
named by module and enclosing function with the reason it is allowed. A listed
downstream site that no longer exists is reported too, so the list cannot go stale.
It prints one line per violation and exits non-zero when there is any.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE_CLASSES = frozenset(
    {
        "RecordingSegmentSource",
        "ContentUnitSource",
        "RecordingSourceRevision",
        "ContentSourceRevision",
        "PinnedRecording",
        "PinnedContent",
        "ArtifactPayloadRef",
        "SourceFileRef",
    }
)
SOURCE_KINDS = frozenset({"recording", "content"})
PAYLOAD_KINDS = frozenset({"artifact", "source_file"})
FORMATS = frozenset({"nuscenes", "lerobot"})

# Module path prefixes upstream of canonical manifests: they define, produce or
# register a source binding, so they dispatch by source by design.
UPSTREAM: dict[str, str] = {
    "packages/sceneops-core/sceneops_core/provenance/": "defines bindings, revisions, clock-origin and identity rules per source kind",
    "packages/sceneops-core/sceneops_core/sources/": "Source Registry contracts (one content under one format)",
    "packages/sceneops-core/sceneops_core/scenes/schemas/": "the Scene manifest / record contracts: a SourceFileRef must name the unit's own content; a record maps its binding",
    "packages/sceneops-core/sceneops_core/episodes/schemas/": "the Episode manifest / record contracts, as for Scenes",
    "packages/sceneops-sources/": "source preservation and the native readers",
    "packages/sceneops-scenes/sceneops_scenes/nuscenes_builder.py": "the nuScenes Scene builder",
    "packages/sceneops-episodes/sceneops_episodes/lerobot_builder.py": "the LeRobot Episode builder",
    "packages/sceneops-db/sceneops_db/": "persistence: per-kind source columns and the registry's conflict rules",
    "apps/worker/sceneops_worker/jobs/dataset/build_snapshot_scenes.py": "selects the Scene builder for a snapshot's format",
    "apps/worker/sceneops_worker/jobs/dataset/build_snapshot_episodes.py": "selects the Episode builder for a snapshot's format",
    "apps/worker/sceneops_worker/scenes/registration.py": "canonical registration: the durability gate per payload kind and the source binding check",
    "apps/worker/sceneops_worker/episodes/registration.py": "canonical registration, as for Scenes",
    "apps/worker/sceneops_worker/sources/": "source snapshot registration and the content gate",
    "apps/publisher/": "publish-snapshot",
}

# Downstream sites, by (module, enclosing function): the payload locator and the
# explicit refusals of consumers that cannot represent a content payload.
DOWNSTREAM: dict[tuple[str, str], str] = {
    (
        "apps/worker/sceneops_worker/payloads.py",
        "PayloadLocator.uris",
    ): "the payload locator: the one dispatch on the payload reference type (MS-10)",
    (
        "packages/sceneops-analytics/sceneops_analytics/tables.py",
        "build_observations_table",
    ): "capability refusal: the observation table has a payload_artifact_id column only (UnsupportedSceneContentError)",
    (
        "packages/sceneops-core/sceneops_core/episodes/alignment/engine.py",
        "require_alignable",
    ): "capability refusal: alignment cannot resolve preserved files (UnsupportedEpisodeContentError, D14)",
    (
        "packages/sceneops-core/sceneops_core/sample_views/build.py",
        "_label_index.resolve",
    ): "identity, not behaviour: a LabelSet anchor names a RobotRun, so it resolves only into a Scene of that RobotRun",
    (
        "packages/sceneops-analytics/sceneops_analytics/external_adapters/lerobot/entrypoint.py",
        "_check_supported",
    ): "an export target format (the adapter's own), not a source format",
}


@dataclass(frozen=True)
class Site:
    module: str
    function: str
    line: int
    what: str


def _names(node: ast.AST) -> set[str]:
    found = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Attribute):
            found.add(n.attr)
    return found


def _strings(node: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _dispatch(node: ast.AST) -> str | None:
    if isinstance(node, ast.Compare):
        names, strings = _names(node), _strings(node)
        if "source_kind" in names:
            return "compares source_kind"
        if "format" in names or strings & FORMATS:
            return "compares a source format"
        if strings & SOURCE_KINDS:
            return f"compares with {sorted(strings & SOURCE_KINDS)}"
        if strings & PAYLOAD_KINDS and "kind" in names:
            return "compares a payload kind"
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("isinstance", "issubclass")
        and len(node.args) == 2
        and _names(node.args[1]) & SOURCE_CLASSES
    ):
        return f"{node.func.id} on {sorted(_names(node.args[1]) & SOURCE_CLASSES)}"
    elif isinstance(node, ast.match_case) and _names(node.pattern) & SOURCE_CLASSES:
        return "match on a source class"
    elif isinstance(node, ast.Dict):
        keys = {
            k.value
            for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if keys & FORMATS:
            return "a dict keyed by source format"
    return None


def sites(path: Path, module: str) -> list[Site]:
    found: list[Site] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        what = _dispatch(node)
        if what is not None:
            line = node.pattern.lineno if isinstance(node, ast.match_case) else node.lineno
            found.append(Site(module, ".".join(scope) or "<module>", line, what))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(ast.parse(path.read_text(), filename=str(path)), ())
    return found


def production_modules(root: Path) -> list[tuple[Path, str]]:
    modules = []
    for top in ("apps", "packages"):
        for path in sorted((root / top).rglob("*.py")):
            module = path.relative_to(root).as_posix()
            parts = module.split("/")
            if (
                "tests" in parts
                or "migrations" in parts
                or path.name == "testing.py"
                or path.stem.endswith("_synthetic")
            ):
                continue
            modules.append((path, module))
    return modules


def check(
    root: Path = REPO_ROOT,
    upstream: dict[str, str] = UPSTREAM,
    downstream: dict[tuple[str, str], str] = DOWNSTREAM,
) -> list[str]:
    violations = []
    used: set[tuple[str, str]] = set()
    for path, module in production_modules(root):
        if any(module.startswith(prefix) for prefix in upstream):
            continue
        for site in sites(path, module):
            key = (site.module, site.function)
            if key in downstream:
                used.add(key)
                continue
            violations.append(
                f"{site.module}:{site.line} ({site.function}): {site.what} downstream of "
                "canonical manifests; only the payload locator dispatches on a "
                "reference type, and a consumer refuses what it cannot represent"
            )
    for module, function in sorted(set(downstream) - used):
        violations.append(
            f"{module} ({function}): listed as an allowed downstream dispatch site, "
            "but no such site exists; remove it from DOWNSTREAM"
        )
    return violations


def main() -> int:
    violations = check()
    for violation in violations:
        print(violation)
    if violations:
        return 1
    print("source dispatch: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
