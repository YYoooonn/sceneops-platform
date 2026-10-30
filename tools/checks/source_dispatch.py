#!/usr/bin/env python
"""Source-kind dispatch stays where ADR-015 puts it (MS-10, MS-13; `make test`).

Downstream of canonical manifests no workflow branches on the source kind, the source
format or the payload namespace: a payload reference (``PayloadRef``) is a Logical URI
with integrity, the same for every source, read through verified access. A consumer
that cannot decode what a reference names (a selector) refuses it by capability, never
by source. Upstream of the manifests (source preservation, the format builders and
importers, canonical, payload and label registration and the contracts that define a
binding) dispatch by source is the point.

Between the two sits anchor resolution (ADR-015 §9): a derived resource (a LabelSet
anchor) names a source by identity, and the one anchor resolver matches that
identity against a Scene's binding. Matching is not dispatch: at a listed IDENTITY
site only a comparison of the whole source identity (``source_kind`` together with
``source_id``, no source-kind, payload-kind or format literal) is allowed, and no
derived workflow (sample views, scenarios, evaluation) compares source identity at
all.

Checked statically on production code (`apps/*/<module>`, `packages/*/<module>`;
tests, `testing.py` and synthetic-tree helpers are not production code). A *dispatch
site* is any of:

  * a comparison that reads a `source_kind` or `format` name or attribute, or compares
    with a source-kind literal ("recording", "content") or a format literal
    ("nuscenes", "lerobot");
  * a comparison with a payload namespace (`LogicalNamespace.SOURCES` /
    `.OBSERVATION_PAYLOADS`, or "sources" / "observation-payloads" next to a
    `namespace`), or a `startswith` / `endswith` test against a Logical URI literal:
    which namespace a URI lies in says which kind of source owns its bytes;
  * an `isinstance` / `issubclass` test, or a `match` class pattern, on a source
    binding, source revision or pinned source class;
  * a dict literal keyed by a format literal (a format registry).

Every site must lie in an UPSTREAM module, be one of the DOWNSTREAM sites below, or
be an identity comparison at one of the IDENTITY sites, each named by module and
enclosing function with the reason it is allowed. A listed site that no longer
exists is reported too, so the lists cannot go stale.
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
    }
)
SOURCE_KINDS = frozenset({"recording", "content"})
NAMESPACE_MEMBERS = frozenset({"SOURCES", "OBSERVATION_PAYLOADS"})
NAMESPACES = frozenset({"sources", "observation-payloads"})
LOGICAL_URI_PREFIX = "sceneops://"
FORMATS = frozenset({"nuscenes", "lerobot"})

# Module path prefixes upstream of canonical manifests: they define, produce or
# register a source binding, so they dispatch by source by design.
UPSTREAM: dict[str, str] = {
    "packages/sceneops-core/sceneops_core/provenance/": "defines bindings, revisions, clock-origin and identity rules per source kind",
    "packages/sceneops-core/sceneops_core/sources/": "Source Registry contracts (one content under one format)",
    "packages/sceneops-core/sceneops_core/scenes/schemas/": "the Scene manifest / record contracts: a record maps its binding",
    "packages/sceneops-core/sceneops_core/episodes/schemas/": "the Episode manifest / record contracts, as for Scenes",
    "packages/sceneops-sources/": "source preservation and the native readers",
    "packages/sceneops-scenes/sceneops_scenes/nuscenes_builder.py": "the nuScenes Scene builder",
    "packages/sceneops-episodes/sceneops_episodes/lerobot_builder.py": "the LeRobot Episode builder",
    "packages/sceneops-db/sceneops_db/": "persistence: per-kind source columns and the registry's conflict rules",
    "apps/worker/sceneops_worker/jobs/dataset/build_snapshot_scenes.py": "selects the Scene builder for a snapshot's format",
    "apps/worker/sceneops_worker/jobs/dataset/build_snapshot_episodes.py": "selects the Episode builder for a snapshot's format",
    "apps/worker/sceneops_worker/scenes/registration.py": "canonical registration: the source binding check and the payload gate of the unit's source",
    "apps/worker/sceneops_worker/payload_registration.py": "the payload registration gate: a reference must name bytes of the unit's own registered source",
    "apps/worker/sceneops_worker/episodes/registration.py": "canonical registration, as for Scenes",
    "apps/worker/sceneops_worker/sources/": "source snapshot registration and the content gate",
    "apps/publisher/": "publish-snapshot",
    "packages/sceneops-derived/sceneops_derived/nuscenes_labels.py": "the nuScenes native label importer (reads annotations from source content)",
    "apps/worker/sceneops_worker/jobs/derived/import_labels.py": "label registration: selects the native label importer for a snapshot's format and checks each anchored source is registered under its kind",
}

# Downstream sites, by (module, enclosing function), each with the reason it is not a
# dispatch by source.
DOWNSTREAM: dict[tuple[str, str], str] = {
    (
        "packages/sceneops-analytics/sceneops_analytics/external_adapters/lerobot/entrypoint.py",
        "_check_supported",
    ): "an export target format (the adapter's own), not a source format",
}


# Anchor resolution, by (module, enclosing function): where a derived anchor's source
# identity is matched against a Scene's binding. Only IDENTITY_MATCH is allowed here.
IDENTITY: dict[tuple[str, str], str] = {
    (
        "packages/sceneops-core/sceneops_core/labels/resolution.py",
        "SceneAnchorResolver.resolve",
    ): "the one label-anchor resolver: an anchor lands in a Scene only when it names the Scene's own source",
}

IDENTITY_MATCH = "compares a whole source identity (source_kind, source_id)"


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
            if "source_id" in names and not strings & (SOURCE_KINDS | FORMATS):
                return IDENTITY_MATCH
            return "compares source_kind"
        if "format" in names or strings & FORMATS:
            return "compares a source format"
        if strings & SOURCE_KINDS:
            return f"compares with {sorted(strings & SOURCE_KINDS)}"
        if names & NAMESPACE_MEMBERS or (strings & NAMESPACES and "namespace" in names):
            return "compares a payload namespace"
        if any(v.startswith(LOGICAL_URI_PREFIX) for v in strings):
            return "compares with a Logical URI"
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("startswith", "endswith")
        and any(v.startswith(LOGICAL_URI_PREFIX) for v in _strings(node))
    ):
        return f"{node.func.attr} on a Logical URI prefix"
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
    identity: dict[tuple[str, str], str] | None = None,
) -> list[str]:
    identity = IDENTITY if identity is None else identity
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
            if key in identity:
                used.add(key)
                if site.what == IDENTITY_MATCH:
                    continue
                violations.append(
                    f"{site.module}:{site.line} ({site.function}): {site.what} at the "
                    "anchor resolver; resolution matches a whole source identity and "
                    "never branches on a source kind, payload kind or format"
                )
                continue
            violations.append(
                f"{site.module}:{site.line} ({site.function}): {site.what} downstream of "
                "canonical manifests; payload references are the same for every "
                "source, only the anchor resolver matches a source identity, and a "
                "consumer refuses what it cannot decode, never a source"
            )
    for module, function in sorted(set(downstream) - used):
        violations.append(
            f"{module} ({function}): listed as an allowed downstream dispatch site, "
            "but no such site exists; remove it from DOWNSTREAM"
        )
    for module, function in sorted(set(identity) - used):
        violations.append(
            f"{module} ({function}): listed as an anchor-resolution site, but no "
            "such site exists; remove it from IDENTITY"
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
