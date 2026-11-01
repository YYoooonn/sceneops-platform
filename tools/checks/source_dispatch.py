#!/usr/bin/env python
"""Source-aware code stays where ADR-015 and ADR-017 put it (MS-10, MS-13;
`make check-boundaries`, `make test`).

Canonical semantic processing (schema validation, alignment, profiling, detection,
analytics, sample views, every canonical consumer) works on what a manifest declares:
clock domains and their origin instances, the declared window, frames, calibrations,
channels and streams, observations and occurrences, and payload references
(``PayloadRef``), a Logical URI with integrity, the same for every source, read through
verified access. It never chooses behavior by the source kind, the source format, the
payload namespace or the source identity. A consumer that cannot decode what a
reference names (a selector) refuses it by capability, never by source.

Source-aware lifecycle code dispatches by source by design: source preservation, the
format builders and importers, canonical, payload and label registration, unit and
revision identity, persistence and the contracts that define a binding (UPSTREAM), and
the few lifecycle functions inside canonical modules that map a binding to the columns
of its record (LIFECYCLE).

Between the two sits anchor resolution (ADR-015 §9): a derived resource (a LabelSet
anchor) names a source by identity, and the one anchor resolver matches that
identity against a Scene's binding. Matching is not dispatch: at a listed IDENTITY
site only a comparison of the whole source identity (``source_kind`` together with
``source_id``, no source-kind, payload-kind or format literal) is allowed, and no
derived workflow (sample views, scenarios, evaluation) compares source identity at
all.

The declared window and the clock-origin authority are carried by the unit's source
binding (``lineage.source``) but are temporal semantics of the manifest: a consumer
reads them through ``declared_window()`` and ``clock_instance(clock)``. Only the
manifest envelope (those accessors and the validators that hold timestamps and clock
declarations to the binding) reads them from the binding, each listed in ENVELOPE.

Checked statically on production code (`apps/*/<module>`, `packages/*/<module>`,
`integrations/*/<module>`; tests, `testing.py`, synthetic-tree helpers and virtual
environments are not production code). A *dispatch site* is any of:

  * a comparison that reads a `source_kind` or `format` name or attribute, or compares
    with a source-kind literal ("recording", "content") or a format literal
    ("nuscenes", "lerobot");
  * a comparison that reads a binding's identity (`source_id`, `robot_run_id`,
    `content_id`, `unit_key`; a binding as defined for binding-time sites below):
    which source a unit came from chooses no behavior. The same columns of a record
    (selection by provenance) are not a binding;
  * a comparison with a payload namespace (`LogicalNamespace.SOURCES` /
    `.OBSERVATION_PAYLOADS`, or "sources" / "observation-payloads" next to a
    `namespace`), or a `startswith` / `endswith` test against a Logical URI literal:
    which namespace a URI lies in says which kind of source owns its bytes (looking a
    namespace up to locate bytes, as the storage resolver does, is not a comparison);
  * an `isinstance` / `issubclass` test, or a `match` class pattern, on a source
    binding, source revision or pinned source class;
  * a dict literal keyed by a format literal (a format registry).

A *binding-time site* is any of:

  * a read of `source_clock`, `start_timestamp_ns`, `end_timestamp_ns` or `contains`
    on a binding, or `getattr` of one of them: a binding is an expression ending in
    `.lineage.source`, or a name bound to one in the same function or an enclosing
    one, by assignment from a binding or by an annotation with a binding class
    (`SourceBinding`, `RecordingSegmentSource`, `ContentUnitSource`). The same fields
    of a declared window, a channel, a stream or a pose are not binding reads;
  * any read of `origin_scope` or `permitted_clock_origins`, which only a binding has.

Name tracking is flow-insensitive and local: it follows assignments to plain names
only, not attributes (`self._source = ...`), containers, return values or calls into
other functions. It is a guard against the direct forms, not a data-flow analysis.

Every site must lie in an UPSTREAM module or be listed, by module and enclosing
function with the reason it is allowed, in the table of its rule: LIFECYCLE or
DOWNSTREAM for a dispatch site, IDENTITY for an identity comparison, ENVELOPE for a
binding-time site. A listing admits only its own rule, so a source-kind branch at an
ENVELOPE site is still a violation. A listed site that no longer exists is reported
too, so the tables cannot go stale.
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

BINDING_CLASSES = frozenset(
    {"SourceBinding", "RecordingSegmentSource", "ContentUnitSource"}
)
# The binding's declared window: fields shared by name with windows, channels,
# streams and poses, so they count only when read from a binding.
BINDING_WINDOW = frozenset(
    {"source_clock", "start_timestamp_ns", "end_timestamp_ns", "contains"}
)
# The binding's clock-origin authority: no other contract has these members.
BINDING_ORIGIN = frozenset({"origin_scope", "permitted_clock_origins"})
# The binding's source identity.
BINDING_IDENTITY = frozenset({"source_id", "robot_run_id", "content_id", "unit_key"})

DISPATCH = "dispatch"
BINDING_TIME = "binding-time"

# Module path prefixes of source-aware lifecycle code: they define, produce, register
# or persist a source binding, so they dispatch by source by design.
UPSTREAM: dict[str, str] = {
    "packages/sceneops-core/sceneops_core/provenance/": "defines bindings, revisions, clock-origin and identity rules per source kind",
    "packages/sceneops-core/sceneops_core/sources/": "Source Registry contracts (one content under one format)",
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
    "integrations/dataset-acquisition/": "converts an external dataset in its own format into a recording; reads no canonical manifest",
}

SCENE_RECORDS = "packages/sceneops-core/sceneops_core/scenes/schemas/records.py"
EPISODE_RECORDS = "packages/sceneops-core/sceneops_core/episodes/schemas/records.py"
SCENE_MANIFESTS = "packages/sceneops-core/sceneops_core/scenes/schemas/manifests.py"
EPISODE_MANIFESTS = "packages/sceneops-core/sceneops_core/episodes/schemas/manifests.py"

# Lifecycle sites inside canonical modules, by (module, enclosing function): a record
# names its source in the column of its source kind.
LIFECYCLE: dict[tuple[str, str], str] = {
    (SCENE_RECORDS, "_source_id"): "a Scene unit record sets exactly the source column of its kind",
    (SCENE_RECORDS, "project_scene_revision"): "projects the binding into the revision record's per-kind source columns",
    (EPISODE_RECORDS, "_source_id"): "an Episode unit record sets exactly the source column of its kind",
    (EPISODE_RECORDS, "project_episode_revision"): "projects the binding into the revision record's per-kind source columns",
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

# The manifest envelope, by (module, enclosing function): the accessors that issue the
# binding's window and origin instance, and the validators that hold the manifest's
# clocks and timestamps to them. Only binding-time sites are allowed here.
ENVELOPE: dict[tuple[str, str], str] = {
    (SCENE_MANIFESTS, "SceneManifest.declared_window"): "the declared window accessor",
    (SCENE_MANIFESTS, "SceneManifest.clock_instance"): "the origin instance accessor: the binding's origin scope",
    (SCENE_MANIFESTS, "SceneManifest._check_clock_domains"): "every clock, the window's included, has an origin the binding permits",
    (SCENE_MANIFESTS, "SceneManifest._check_source_window"): "every timestamp on the window's clock lies in the window",
    (EPISODE_MANIFESTS, "EpisodeManifest.declared_window"): "the declared window accessor",
    (EPISODE_MANIFESTS, "EpisodeManifest.clock_instance"): "the origin instance accessor: the binding's origin scope",
    (EPISODE_MANIFESTS, "EpisodeManifest._check_clock_domains"): "every clock, the window's included, has an origin the binding permits",
    (EPISODE_MANIFESTS, "EpisodeManifest._check_source_window"): "every timestamp on the window's clock lies in the window",
    (EPISODE_MANIFESTS, "EpisodeManifest._check_frame_timeline"): "a frame timeline is on the window's clock and spans exactly the window",
}

IDENTITY_MATCH = "compares a whole source identity (source_kind, source_id)"


@dataclass(frozen=True)
class Site:
    module: str
    function: str
    line: int
    what: str
    rule: str = DISPATCH


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


def _is_binding(node: ast.AST, aliases: frozenset[str]) -> bool:
    if isinstance(node, ast.NamedExpr):
        return _is_binding(node.value, aliases)
    if isinstance(node, ast.Attribute):
        return node.attr == "source" and (
            isinstance(node.value, ast.Attribute) and node.value.attr == "lineage"
        )
    return isinstance(node, ast.Name) and node.id in aliases


def _annotates_binding(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval")
        except SyntaxError:
            return False
    return bool(_names(annotation) & BINDING_CLASSES)


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """The nodes of ``scope`` outside nested functions and classes."""
    found: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        found.append(node)
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            pending.extend(ast.iter_child_nodes(node))
    return found


def _aliases(scope: ast.AST, inherited: frozenset[str]) -> frozenset[str]:
    """Names bound to a binding in ``scope``: parameters annotated with a binding
    class, and plain names assigned a binding (to a fixed point, so an alias of an
    alias counts), on top of the enclosing function's."""
    aliases = set(inherited)
    nodes = _own_nodes(scope)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = scope.args
        for arg in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *filter(None, (arguments.vararg, arguments.kwarg)),
        ):
            if _annotates_binding(arg.annotation):
                aliases.add(arg.arg)
    while True:
        found = set(aliases)
        for node in nodes:
            if isinstance(node, ast.Assign) and _is_binding(
                node.value, frozenset(aliases)
            ):
                found.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _annotates_binding(node.annotation) or (
                    node.value is not None
                    and _is_binding(node.value, frozenset(aliases))
                ):
                    found.add(node.target.id)
            elif isinstance(node, ast.NamedExpr) and _is_binding(
                node.value, frozenset(aliases)
            ):
                found.add(node.target.id)
        if found == aliases:
            return frozenset(aliases)
        aliases = found


def _binding_identity(node: ast.AST, aliases: frozenset[str]) -> str | None:
    if isinstance(node, ast.Compare):
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Attribute)
                and n.attr in BINDING_IDENTITY
                and _is_binding(n.value, aliases)
            ):
                return f"compares the binding's {n.attr}"
    return None


def _binding_time(node: ast.AST, aliases: frozenset[str]) -> str | None:
    if isinstance(node, ast.Attribute):
        if node.attr in BINDING_ORIGIN:
            return f"reads the binding's {node.attr}"
        if node.attr in BINDING_WINDOW and _is_binding(node.value, aliases):
            return f"reads the binding's {node.attr}"
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and _is_binding(node.args[0], aliases)
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in BINDING_WINDOW | BINDING_ORIGIN
    ):
        return f"reads the binding's {node.args[1].value}"
    return None


def sites(path: Path, module: str) -> list[Site]:
    found: list[Site] = []

    def visit(node: ast.AST, scope: tuple[str, ...], aliases: frozenset[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
            aliases = _aliases(node, aliases)
        elif isinstance(node, ast.ClassDef):
            # A method does not see names bound in its class body.
            scope = (*scope, node.name)
            aliases = frozenset()
        function = ".".join(scope) or "<module>"
        what = _dispatch(node) or _binding_identity(node, aliases)
        if what is not None:
            line = node.pattern.lineno if isinstance(node, ast.match_case) else node.lineno
            found.append(Site(module, function, line, what))
        what = _binding_time(node, aliases)
        if what is not None:
            found.append(Site(module, function, node.lineno, what, BINDING_TIME))
        for child in ast.iter_child_nodes(node):
            visit(child, scope, aliases)

    tree = ast.parse(path.read_text(), filename=str(path))
    visit(tree, (), _aliases(tree, frozenset()))
    return found


def production_modules(root: Path) -> list[tuple[Path, str]]:
    modules = []
    for top in ("apps", "packages", "integrations"):
        for path in sorted((root / top).rglob("*.py")):
            module = path.relative_to(root).as_posix()
            parts = module.split("/")
            if (
                "tests" in parts
                or "migrations" in parts
                or any(part.startswith(".") for part in parts)
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
    lifecycle: dict[tuple[str, str], str] | None = None,
    envelope: dict[tuple[str, str], str] | None = None,
) -> list[str]:
    identity = IDENTITY if identity is None else identity
    lifecycle = LIFECYCLE if lifecycle is None else lifecycle
    envelope = ENVELOPE if envelope is None else envelope
    violations = []
    used: set[tuple[str, tuple[str, str]]] = set()
    for path, module in production_modules(root):
        if any(module.startswith(prefix) for prefix in upstream):
            continue
        for site in sites(path, module):
            key = (site.module, site.function)
            where = f"{site.module}:{site.line} ({site.function}): {site.what}"
            if site.rule == BINDING_TIME:
                if key in envelope:
                    used.add(("ENVELOPE", key))
                    continue
                violations.append(
                    f"{where} outside the manifest envelope; the declared window is "
                    "read through declared_window() and an origin instance through "
                    "clock_instance(clock), never from the source binding"
                )
                continue
            if key in downstream:
                used.add(("DOWNSTREAM", key))
                continue
            if key in lifecycle:
                used.add(("LIFECYCLE", key))
                continue
            if key in identity:
                used.add(("IDENTITY", key))
                if site.what == IDENTITY_MATCH:
                    continue
                violations.append(
                    f"{where} at the anchor resolver; resolution matches a whole "
                    "source identity and never branches on a source kind, payload "
                    "kind or format"
                )
                continue
            violations.append(
                f"{where} downstream of canonical manifests; payload references are "
                "the same for every source, only the anchor resolver matches a source "
                "identity, and a consumer refuses what it cannot decode, never a "
                "source"
            )
    for table, listed, kind in (
        ("DOWNSTREAM", downstream, "an allowed downstream dispatch site"),
        ("LIFECYCLE", lifecycle, "a lifecycle dispatch site"),
        ("IDENTITY", identity, "an anchor-resolution site"),
        ("ENVELOPE", envelope, "a manifest envelope site"),
    ):
        for module, function in sorted(set(listed) - {k for t, k in used if t == table}):
            violations.append(
                f"{module} ({function}): listed as {kind}, but no such site exists; "
                f"remove it from {table}"
            )
    return violations


def main() -> int:
    violations = check()
    for violation in violations:
        print(violation)
    if violations:
        return 1
    print(
        f"source dispatch: ok ({len(production_modules(REPO_ROOT))} production "
        "modules; source dispatch and binding time outside the listed sites: none)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
