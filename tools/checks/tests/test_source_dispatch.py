"""The source-dispatch check rejects what it claims to reject, and the repository
satisfies it (ADR-015 MS-10, ADR-017).

Synthetic modules exercise each rule in isolation. Protected-fixture tests copy real
production modules into a temporary tree, check them under the repository's own
tables, and then inject one violation into a copy, so they prove the classification
of the real code, not only of a toy."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "source_dispatch", REPO_ROOT / "tools" / "checks" / "source_dispatch.py"
)
assert _SPEC and _SPEC.loader
dispatch = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = dispatch
_SPEC.loader.exec_module(dispatch)

CONSUMER = "packages/sceneops-x/sceneops_x/consumer.py"

SCENE_MANIFESTS = dispatch.SCENE_MANIFESTS
EPISODE_MANIFESTS = dispatch.EPISODE_MANIFESTS
SCENE_RECORDS = dispatch.SCENE_RECORDS
EPISODE_RECORDS = dispatch.EPISODE_RECORDS
PROFILER = "packages/sceneops-episodes/sceneops_episodes/profiling/profiler.py"
ALIGNMENT = "packages/sceneops-core/sceneops_core/episodes/alignment/engine.py"
ALIGN_JOB = "apps/worker/sceneops_worker/jobs/dataset/align_episode.py"
RESOLVER = "packages/sceneops-core/sceneops_core/labels/resolution.py"
WORKER_PAYLOADS = "apps/worker/sceneops_worker/payloads.py"
STORAGE_PAYLOADS = "packages/sceneops-storage/sceneops_storage/payloads.py"
SCENE_REGISTRATION = "apps/worker/sceneops_worker/scenes/registration.py"
UNIT_IDENTITY = "packages/sceneops-core/sceneops_core/provenance/identity.py"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


def _isolated(root: Path, **tables: dict) -> list[str]:
    """The check with only the given tables (the others empty)."""
    for name in ("upstream", "downstream", "identity", "lifecycle", "envelope"):
        tables.setdefault(name, {})
    return dispatch.check(root, **tables)


def test_the_repository_dispatches_on_source_only_where_allowed():
    assert dispatch.check() == []


# --- source dispatch ----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "def f(m):\n    return m.lineage.source.source_kind == 'content'\n",
        "def f(record):\n    if record.source_kind != 'recording':\n        return 1\n",
        "def f(unit):\n    return ('recording', unit.robot_run_id) == unit.key\n",
        "def f(src):\n    return isinstance(src, PinnedContent)\n",
        "def f(src):\n    return isinstance(src, (RecordingSegmentSource, int))\n",
        "def f(src):\n    match src:\n        case ContentUnitSource():\n            return 1\n",
        "def f(ref):\n    return ref.logical_uri().namespace == LogicalNamespace.SOURCES\n",
        "def f(u):\n    return u.namespace != 'observation-payloads'\n",
        "def f(ref):\n    return ref.uri.startswith('sceneops://sources/')\n",
        "def f(ref):\n    return ref.uri[:19] == 'sceneops://sources/'\n",
        "def f(snapshot):\n    return snapshot.format == 'nuscenes'\n",
        "READERS = {'lerobot': 1, 'nuscenes': 2}\n",
        # Source identity read from the binding chooses behavior.
        "def f(m):\n    return m.lineage.source.robot_run_id == 'run-7'\n",
        "def f(m):\n    source = m.lineage.source\n    return source.source_id in SKIP\n",
        "def f(b: ContentUnitSource):\n    if b.content_id != KNOWN:\n        return 1\n",
        "def f(m):\n    return m.lineage.source.unit_key.startswith('whole_recording')\n"
        "def g(m):\n    return m.lineage.source.unit_key == 'k'\n",
    ],
)
def test_a_downstream_dispatch_site_is_rejected(tmp_path, body):
    root = _repo(tmp_path, {CONSUMER: body})
    (violation,) = _isolated(root)
    assert violation.startswith(CONSUMER)


def test_ordinary_code_is_not_a_dispatch_site(tmp_path):
    body = (
        "def f(store, clause, ref, record):\n"
        "    rows = store.list(source_kind=clause.source_kind, source_id=clause.source_id)\n"
        "    mine = [r for r in rows if r.robot_run_id == clause.robot_run_id]\n"
        "    return rows, mine, ref.media_type == 'image/jpeg', ref.kind\n"
    )
    root = _repo(tmp_path, {CONSUMER: body})
    assert _isolated(root) == []


IDENTITY_BODY = (
    "class R:\n"
    "    def resolve(self, anchor):\n"
    "        return (anchor.source_kind, anchor.source_id) != self.source\n"
)


def test_an_identity_match_is_allowed_only_at_the_anchor_resolver(tmp_path):
    root = _repo(tmp_path, {CONSUMER: IDENTITY_BODY})
    assert _isolated(root, identity={(CONSUMER, "R.resolve"): "x"}) == []
    # The same comparison in any other derived workflow is a violation.
    (violation,) = _isolated(root)
    assert "only the anchor resolver matches a source identity" in violation


@pytest.mark.parametrize(
    "comparison",
    [
        "('recording', anchor.source_id) == (anchor.source_kind, anchor.source_id)",
        "anchor.source_kind == 'content' and anchor.source_id == self.source_id",
        "anchor.source_kind != self.kind",
    ],
)
def test_the_anchor_resolver_may_not_branch_on_a_source_kind(tmp_path, comparison):
    body = f"class R:\n    def resolve(self, anchor):\n        return {comparison}\n"
    root = _repo(tmp_path, {CONSUMER: body})
    violations = _isolated(root, identity={(CONSUMER, "R.resolve"): "x"})
    assert violations and all("anchor resolver" in v for v in violations)


def test_a_listed_identity_site_that_no_longer_exists_is_reported(tmp_path):
    root = _repo(tmp_path, {CONSUMER: "def f():\n    return 1\n"})
    (violation,) = _isolated(root, identity={(CONSUMER, "f"): "x"})
    assert "remove it from IDENTITY" in violation


def test_upstream_modules_and_listed_sites_are_allowed(tmp_path):
    root = _repo(
        tmp_path,
        {
            "packages/sceneops-up/sceneops_up/builder.py": "X = {'nuscenes': 1}\n",
            CONSUMER: "class L:\n    def uris(self, r):\n        return isinstance(r, PinnedContent)\n",
            "packages/sceneops-x/tests/test_consumer.py": "Y = {'lerobot': 1}\n",
        },
    )
    assert (
        _isolated(
            root,
            upstream={"packages/sceneops-up/": "a builder"},
            downstream={(CONSUMER, "L.uris"): "the locator"},
        )
        == []
    )


def test_a_listed_site_that_no_longer_exists_is_reported(tmp_path):
    root = _repo(tmp_path, {CONSUMER: "def f():\n    return 1\n"})
    (violation,) = _isolated(root, downstream={(CONSUMER, "f"): "was a refusal"})
    assert "no such site exists" in violation


def test_a_site_moved_to_another_function_is_not_covered_by_the_listing(tmp_path):
    body = "def g(src):\n    return isinstance(src, PinnedContent)\n"
    root = _repo(tmp_path, {CONSUMER: body})
    violations = _isolated(root, downstream={(CONSUMER, "f"): "x"})
    assert len(violations) == 2


def test_a_lifecycle_site_admits_dispatch_but_not_binding_time(tmp_path):
    body = (
        "def project(manifest):\n"
        "    source = manifest.lineage.source\n"
        "    kind = source.source_id if source.source_kind == 'recording' else None\n"
        "    return kind, source.start_timestamp_ns\n"
    )
    root = _repo(tmp_path, {CONSUMER: body})
    (violation,) = _isolated(root, lifecycle={(CONSUMER, "project"): "x"})
    assert "start_timestamp_ns outside the manifest envelope" in violation


def test_a_listed_lifecycle_site_that_no_longer_exists_is_reported(tmp_path):
    root = _repo(tmp_path, {CONSUMER: "def f():\n    return 1\n"})
    (violation,) = _isolated(root, lifecycle={(CONSUMER, "f"): "x"})
    assert "remove it from LIFECYCLE" in violation


def test_integration_code_is_production_code_but_virtual_environments_are_not(
    tmp_path,
):
    body = "def f(ref):\n    return ref.uri.startswith('sceneops://sources/')\n"
    root = _repo(
        tmp_path,
        {
            "integrations/x-server/x_server/handler.py": body,
            "integrations/x-server/.venv/lib/site.py": body,
            "integrations/x-server/tests/test_handler.py": body,
        },
    )
    (violation,) = _isolated(root)
    assert violation.startswith("integrations/x-server/x_server/handler.py")


# --- binding time -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        (
            "def f(manifest):\n    start = manifest.lineage.source.start_timestamp_ns\n"
            "    return start\n",
            ["start_timestamp_ns"],
        ),
        (
            "def f(manifest):\n    return (\n"
            "        manifest.lineage.source.end_timestamp_ns\n"
            "        - manifest.lineage.source.start_timestamp_ns\n    )\n",
            ["end_timestamp_ns", "start_timestamp_ns"],
        ),
        (
            "def f(manifest):\n    return manifest.lineage.source.source_clock\n",
            ["source_clock"],
        ),
        (
            "def f(manifest, t):\n    return manifest.lineage.source.contains(t)\n",
            ["contains"],
        ),
        # A plain-name alias, an alias of an alias, and a walrus.
        (
            "def f(manifest):\n    binding = manifest.lineage.source\n"
            "    start = binding.start_timestamp_ns\n    return start\n",
            ["start_timestamp_ns"],
        ),
        (
            "def f(manifest):\n    a = manifest.lineage.source\n    b = a\n"
            "    return b.end_timestamp_ns\n",
            ["end_timestamp_ns"],
        ),
        (
            "def f(m):\n    if (s := m.lineage.source).source_clock:\n"
            "        return s.end_timestamp_ns\n",
            ["source_clock", "end_timestamp_ns"],
        ),
        # A parameter or variable annotated with a binding class.
        (
            "def f(binding: SourceBinding):\n    return binding.end_timestamp_ns\n",
            ["end_timestamp_ns"],
        ),
        (
            "def f(b: 'RecordingSegmentSource | None'):\n    return b.source_clock\n",
            ["source_clock"],
        ),
        (
            "def f(x):\n    b: ContentUnitSource = x\n    return b.start_timestamp_ns\n",
            ["start_timestamp_ns"],
        ),
        # A closure sees its enclosing function's alias.
        (
            "def f(manifest):\n    source = manifest.lineage.source\n"
            "    def inner():\n        return source.source_clock\n    return inner\n",
            ["source_clock"],
        ),
        (
            "def f(manifest):\n"
            "    return getattr(manifest.lineage.source, 'start_timestamp_ns')\n",
            ["start_timestamp_ns"],
        ),
        # Origin authority: only a binding has these, whatever the receiver.
        (
            "def f(binding):\n    return binding.origin_scope()\n",
            ["origin_scope"],
        ),
        (
            "def f(m):\n    return m.lineage.source.permitted_clock_origins\n",
            ["permitted_clock_origins"],
        ),
    ],
)
def test_a_downstream_binding_time_read_is_rejected(tmp_path, body, fields):
    root = _repo(tmp_path, {CONSUMER: body})
    violations = _isolated(root)
    assert len(violations) == len(fields)
    for violation, field in zip(violations, fields, strict=True):
        assert violation.startswith(CONSUMER)
        assert f"reads the binding's {field} outside the manifest envelope" in violation


@pytest.mark.parametrize(
    "body",
    [
        # The manifest's accessors, and the same field names on what they return.
        "def f(manifest):\n    window = manifest.declared_window()\n"
        "    return window.source_clock, window.end_timestamp_ns - window.start_timestamp_ns\n",
        "def f(manifest, clock):\n    return manifest.clock_instance(clock)\n",
        "def f(manifest):\n    return manifest.declared_window().source_clock\n",
        # Clocks of channels, streams, poses and aligned outputs.
        "def f(m):\n    return [c.source_clock for c in m.channels], m.streams[0].source_clock\n",
        "def f(aligned):\n    return aligned.source_clock\n",
        # Building a binding names the fields as keywords; it reads none.
        "def f(pinned, w):\n    return pinned.segment_source(\n"
        "        source_clock=w.clock, start_timestamp_ns=w.start, end_timestamp_ns=w.end,\n"
        "        unit_key='k')\n",
        # Strings, comments and docstrings are not reads.
        "def f():\n    '''Never read lineage.source.start_timestamp_ns.'''\n"
        "    # manifest.lineage.source.source_clock\n"
        "    return 'lineage.source.end_timestamp_ns'\n",
        # Passing the binding on (unit identity) reads none of its time fields.
        "def f(manifest):\n    return scene_id_for(manifest.lineage.source)\n",
        # An alias in one function is not an alias in another, nor in a method.
        "def f(m):\n    source = m.lineage.source\n    return source\n"
        "def g(source):\n    return source.source_clock\n",
        "class C:\n    source = None\n    def f(self, source):\n        return source.source_clock\n",
        # Another lineage-like attribute is not the binding.
        "def f(m):\n    return m.lineage.producer.source_clock, m.source.end_timestamp_ns\n",
    ],
)
def test_reading_through_the_manifest_is_not_a_binding_time_read(tmp_path, body):
    root = _repo(tmp_path, {CONSUMER: body})
    assert _isolated(root) == []


ENVELOPE_BODY = (
    "class M:\n"
    "    def declared_window(self):\n"
    "        source = self.lineage.source\n"
    "        return source.source_clock, source.start_timestamp_ns\n"
)


def test_a_binding_time_read_is_allowed_only_at_an_envelope_site(tmp_path):
    root = _repo(tmp_path, {CONSUMER: ENVELOPE_BODY})
    assert _isolated(root, envelope={(CONSUMER, "M.declared_window"): "x"}) == []
    assert len(_isolated(root)) == 2
    # Listing another function of the module does not cover it.
    violations = _isolated(root, envelope={(CONSUMER, "M.clock_instance"): "x"})
    assert len(violations) == 3
    assert "remove it from ENVELOPE" in violations[-1]


def test_an_envelope_site_admits_no_source_dispatch(tmp_path):
    body = (
        "class M:\n"
        "    def _check_source_window(self):\n"
        "        source = self.lineage.source\n"
        "        if source.source_kind == 'recording':\n"
        "            return source.contains(0)\n"
    )
    root = _repo(tmp_path, {CONSUMER: body})
    (violation,) = _isolated(root, envelope={(CONSUMER, "M._check_source_window"): "x"})
    assert "compares source_kind downstream of canonical manifests" in violation


# --- policy -------------------------------------------------------------------------

CANONICAL_MODULES = (
    SCENE_MANIFESTS,
    EPISODE_MANIFESTS,
    SCENE_RECORDS,
    EPISODE_RECORDS,
    "packages/sceneops-core/sceneops_core/scenes/schemas/runs.py",
    "packages/sceneops-core/sceneops_core/episodes/schemas/enums.py",
    "packages/sceneops-core/sceneops_core/episodes/schemas/fields.py",
    PROFILER,
    ALIGNMENT,
    RESOLVER,
)


@pytest.mark.parametrize("module", CANONICAL_MODULES)
def test_no_upstream_prefix_covers_a_canonical_module(module):
    assert (REPO_ROOT / module).is_file()
    assert not [p for p in dispatch.UPSTREAM if module.startswith(p)]


def test_every_listed_site_lies_outside_upstream():
    """A listing under an UPSTREAM prefix would never be consulted, so it would
    claim a narrow allowance the prefix silently widens."""
    tables = (
        dispatch.LIFECYCLE,
        dispatch.DOWNSTREAM,
        dispatch.IDENTITY,
        dispatch.ENVELOPE,
    )
    for table in tables:
        for module, _ in table:
            assert not [p for p in dispatch.UPSTREAM if module.startswith(p)], module


def test_every_exemption_states_its_reason():
    tables = (
        dispatch.UPSTREAM,
        dispatch.LIFECYCLE,
        dispatch.DOWNSTREAM,
        dispatch.IDENTITY,
        dispatch.ENVELOPE,
    )
    assert all(reason.strip() for table in tables for reason in table.values())


# --- protected fixtures: real modules under the repository's tables ------------------


def _protected(
    tmp_path: Path, modules: tuple[str, ...], edits: dict[str, tuple[str, str]]
) -> list[str]:
    """Copies ``modules`` into a temporary tree, applies each ``module -> (old,
    new)`` edit (``old`` must occur exactly once), and returns the violations
    of the copied modules under the real tables (listings of modules not copied
    are reported stale and dropped here)."""
    files = {}
    for module in modules:
        text = (REPO_ROOT / module).read_text()
        if module in edits:
            old, new = edits[module]
            assert text.count(old) == 1, f"{module}: anchor {old!r} moved"
            text = text.replace(old, new)
        files[module] = text
    root = _repo(tmp_path, files)
    return [v for v in dispatch.check(root) if v.startswith(modules)]


POSITIVE = (
    # Source-aware registration: the source binding check and payload gate.
    SCENE_REGISTRATION,
    # Unit identity: source kind is an identity input.
    UNIT_IDENTITY,
    # Native label anchors: identity matching at the one resolver.
    RESOLVER,
    # The manifest envelope: origin validation and the window / instance accessors.
    SCENE_MANIFESTS,
    EPISODE_MANIFESTS,
    # Lifecycle records: per-kind source columns.
    SCENE_RECORDS,
    EPISODE_RECORDS,
    # Storage URI resolution: a namespace is looked up to locate bytes.
    STORAGE_PAYLOADS,
    # Capability-based processing: selectors, media types, declared window.
    WORKER_PAYLOADS,
    ALIGNMENT,
    PROFILER,
    ALIGN_JOB,
)


def test_legitimate_source_aware_and_capability_code_is_accepted(tmp_path):
    assert _protected(tmp_path, POSITIVE, {}) == []


@pytest.mark.parametrize(
    ("module", "table"),
    [
        (SCENE_REGISTRATION, None),
        (UNIT_IDENTITY, None),
        (RESOLVER, dispatch.IDENTITY),
        (SCENE_MANIFESTS, dispatch.ENVELOPE),
        (EPISODE_MANIFESTS, dispatch.ENVELOPE),
        (SCENE_RECORDS, dispatch.LIFECYCLE),
        (EPISODE_RECORDS, dispatch.LIFECYCLE),
    ],
)
def test_source_aware_code_is_accepted_by_classification_not_by_blindness(
    module, table
):
    """The positive controls hold real sites: they pass because of where they
    are, an UPSTREAM module or a listed function, not because nothing is seen."""
    found = dispatch.sites(REPO_ROOT / module, module)
    assert found
    for site in found:
        if table is None:
            assert any(module.startswith(p) for p in dispatch.UPSTREAM)
        else:
            assert (site.module, site.function) in table


@pytest.mark.parametrize(
    "module", [STORAGE_PAYLOADS, WORKER_PAYLOADS, PROFILER, ALIGNMENT]
)
def test_resolution_and_capability_checks_hold_no_site_and_no_exemption(module):
    assert dispatch.sites(REPO_ROOT / module, module) == []
    assert not [p for p in dispatch.UPSTREAM if module.startswith(p)]


# Each case: (module, (anchor, replacement), function, expected site description).
NEGATIVE = {
    "source kind in Scene validation": (
        SCENE_MANIFESTS,
        (
            "    def _check_references(self) -> None:\n",
            "    def _check_references(self) -> None:\n"
            "        if self.lineage.source.source_kind == 'recording':\n"
            "            return\n",
        ),
        "SceneManifest._check_references",
        "compares source_kind",
    ),
    "source kind in a Scene envelope validator": (
        SCENE_MANIFESTS,
        (
            "        source = self.lineage.source\n        for clock, timestamp",
            "        source = self.lineage.source\n"
            "        if source.source_kind == 'content':\n"
            "            return\n"
            "        for clock, timestamp",
        ),
        "SceneManifest._check_source_window",
        "compares source_kind",
    ),
    "source kind in Episode validation": (
        EPISODE_MANIFESTS,
        (
            "    def _check_invariants(self) -> EpisodeManifest:\n",
            "    def _check_invariants(self) -> EpisodeManifest:\n"
            "        if self.lineage.source.source_kind == 'content':\n"
            "            return self\n",
        ),
        "EpisodeManifest._check_invariants",
        "compares source_kind",
    ),
    "binding class test in an Episode envelope validator": (
        EPISODE_MANIFESTS,
        (
            "        if source.source_clock != timeline.clock:\n",
            "        if isinstance(source, ContentUnitSource):\n"
            "            return\n"
            "        if source.source_clock != timeline.clock:\n",
        ),
        "EpisodeManifest._check_frame_timeline",
        "isinstance on ['ContentUnitSource']",
    ),
    "binding time read in Episode validation": (
        EPISODE_MANIFESTS,
        (
            "        self._check_clock_domains()\n",
            "        self._check_clock_domains()\n"
            "        start = self.lineage.source.start_timestamp_ns\n",
        ),
        "EpisodeManifest._check_invariants",
        "reads the binding's start_timestamp_ns",
    ),
    "start read in the Episode profiler": (
        PROFILER,
        (
            "        window = manifest.declared_window()\n",
            "        window = manifest.declared_window()\n"
            "        start = manifest.lineage.source.start_timestamp_ns\n",
        ),
        "EpisodeManifestProfiler.profile",
        "reads the binding's start_timestamp_ns",
    ),
    "the binding aliased as the window in the profiler": (
        PROFILER,
        (
            "        window = manifest.declared_window()\n",
            "        window = manifest.lineage.source\n",
        ),
        "EpisodeManifestProfiler.profile",
        "reads the binding's end_timestamp_ns",
    ),
    "end read in alignment": (
        ALIGNMENT,
        (
            "    window = manifest.declared_window()\n",
            "    window = manifest.declared_window()\n"
            "    last = manifest.lineage.source.end_timestamp_ns\n",
        ),
        "align_episode",
        "reads the binding's end_timestamp_ns",
    ),
    "source clock read in ALIGN_EPISODE": (
        ALIGN_JOB,
        (
            "source_clock=manifest.declared_window().source_clock",
            "source_clock=manifest.lineage.source.source_clock",
        ),
        "AlignEpisodeJobHandler._align_one",
        "reads the binding's source_clock",
    ),
    "origin scope at the anchor resolver": (
        RESOLVER,
        (
            "        self._source = (source.source_kind, source.source_id)\n",
            "        self._source = (source.source_kind, source.source_id)\n"
            "        self._scope = source.origin_scope()\n",
        ),
        "SceneAnchorResolver.__init__",
        "reads the binding's origin_scope",
    ),
    "payload namespace as processing dispatch": (
        WORKER_PAYLOADS,
        (
            '    """Refuse every reference that selects part of its object."""\n',
            '    """Refuse every reference that selects part of its object."""\n'
            "    refs = [r for r in refs if parse_logical_uri(r.uri).namespace\n"
            "            != LogicalNamespace.SOURCES]\n",
        ),
        "require_whole_payloads",
        "compares a payload namespace",
    ),
    "Logical URI prefix as processing dispatch": (
        WORKER_PAYLOADS,
        (
            '    """Refuse every reference that selects part of its object."""\n',
            '    """Refuse every reference that selects part of its object."""\n'
            "    if any(r.uri.startswith('sceneops://observation-payloads/') for r in refs):\n"
            "        return\n",
        ),
        "require_whole_payloads",
        "startswith on a Logical URI prefix",
    ),
    "source branch in the protected profiler": (
        PROFILER,
        (
            "        streams: list[EpisodeStreamProfile] = []\n",
            "        streams: list[EpisodeStreamProfile] = []\n"
            "        if manifest.lineage.source.source_kind == 'recording':\n"
            "            streams = []\n",
        ),
        "EpisodeManifestProfiler.profile",
        "compares source_kind",
    ),
    "source identity branch in the protected alignment engine": (
        ALIGNMENT,
        (
            "    window = manifest.declared_window()\n",
            "    window = manifest.declared_window()\n"
            "    if manifest.lineage.source.robot_run_id == 'run-1':\n"
            "        window = None\n",
        ),
        "align_episode",
        "compares the binding's robot_run_id",
    ),
}


@pytest.mark.parametrize("case", sorted(NEGATIVE))
def test_a_violation_injected_into_protected_code_is_rejected(tmp_path, case):
    module, edit, function, what = NEGATIVE[case]
    # The unmodified copy is clean, so the violation is the injected one.
    assert _protected(tmp_path / "before", (module,), {}) == []
    violations = _protected(tmp_path / "after", (module,), {module: edit})
    assert violations, case
    assert all(f"({function}): " in v for v in violations), violations
    assert any(f"({function}): {what}" in v for v in violations), violations


# ── clock scope opacity ──────────────────────────────────────────────────────

SCOPE_MUTATION = "packages/sceneops-x/sceneops_x/scope_consumer.py"

SCOPE_READS = {
    "subscript": "def f(clock_scope, other):\n    return clock_scope[0] == other\n",
    "subscript_literal": 'def f(clock_scope):\n    if clock_scope[0] == "recording":\n        pass\n',
    "instance_literal": 'def f(instance):\n    if instance.scope[0] == "content":\n        pass\n',
    "negative_index": "def f(clock_scope):\n    return clock_scope[-1]\n",
    "slice": "def f(clock_scope):\n    return clock_scope[:1]\n",
    "row_key": 'def f(row):\n    return row["clock_scope"][0]\n',
    "row_window_key": 'def f(row):\n    return row["window_clock_scope"][0]\n',
    "row_get": 'def f(row):\n    return row.get("clock_scope")[0]\n',
    "attribute": "def f(ae):\n    return ae.clock_scope[0]\n",
    "window_attribute": "def f(obj):\n    return obj.window_clock_scope[0]\n",
    "named_instance": "def f(instance):\n    return instance.scope[0]\n",
    "suffixed_instance": "def f(window_instance):\n    return window_instance.scope[0]\n",
    "annotated_instance": "def f(x: ClockInstance):\n    return x.scope[0]\n",
    "string_annotation": 'def f(x: "ClockInstance"):\n    return x.scope[0]\n',
    "property": "def f(ae):\n    return ae.clock_instance.scope[0]\n",
    "accessor": "def f(m, c):\n    return m.clock_instance(c).scope[0]\n",
    "restored": "def f(c, o, s):\n    return restore_clock_instance(c, o, s).scope[0]\n",
    "converted": "def f(instance):\n    return list(instance.scope)[0]\n",
    "tuple_converted": "def f(clock_scope):\n    return tuple(clock_scope)[0]\n",
    "unpack": "def f(clock_scope):\n    kind, source_id = clock_scope\n",
    "unpack_star": "def f(clock_scope):\n    kind, *rest = clock_scope\n",
    "unpack_list": "def f(clock_scope):\n    [kind, source_id] = clock_scope\n",
    "unpack_instance": "def f(x: ClockInstance):\n    kind, source_id = x.scope\n",
    "unpack_converted": "def f(instance):\n    kind, source_id = list(instance.scope)\n",
    "alias": "def f(instance):\n    scope = instance.scope\n    return scope[0]\n",
    "alias_of_alias": "def f(clock_scope):\n    a = clock_scope\n    b = a\n    return b[0]\n",
    "alias_converted": "def f(instance):\n    scope = tuple(instance.scope)\n    kind, _ = scope\n",
    "alias_of_instance": "def f(m, c):\n    i = m.clock_instance(c)\n    return i.scope[0]\n",
    "walrus": "def f(row):\n    if (s := row['clock_scope']):\n        return s[0]\n",
    "enclosing_alias": "def f(instance):\n    scope = instance.scope\n    def g():\n        return scope[0]\n    return g\n",
    "for": "def f(clock_scope):\n    for element in clock_scope:\n        pass\n",
    "comprehension": "def f(clock_scope):\n    return [e for e in clock_scope]\n",
    "next_iter": "def f(clock_scope):\n    return next(iter(clock_scope))\n",
    "enumerate": "def f(clock_scope):\n    return list(enumerate(clock_scope))\n",
    "spread": "def f(clock_scope):\n    g(*clock_scope)\n",
    "membership": "def f(clock_scope, x):\n    return x in clock_scope\n",
    "not_membership": "def f(clock_scope, x):\n    return x not in clock_scope\n",
    "match": "def f(clock_scope):\n    match clock_scope:\n        case [kind, _]:\n            return kind\n",
    "polars": 'def f(df):\n    return df.select(pl.col("clock_scope").list.get(0))\n',
    "polars_first": 'def f(df):\n    return df.select(pl.col("window_clock_scope").list.first())\n',
    "sql_subscript": 'def f(con):\n    return con.execute("SELECT clock_scope[1] FROM observations")\n',
    "sql_extract": 'def f(con):\n    return con.execute("SELECT list_extract(window_clock_scope, 1) FROM scenes")\n',
    "sql_unnest": 'def f(con):\n    return con.execute("SELECT unnest(clock_scope) FROM keyframes")\n',
}

SCOPE_ALLOWED = {
    "whole_equality": "def f(a, b):\n    return a.clock_scope == b.clock_scope\n",
    "whole_instance": "def f(a, b):\n    require_comparable(a.clock_instance, b.clock_instance)\n",
    "restore": 'def f(row):\n    return restore_clock_instance(row["source_clock"], row["clock_origin"], row["clock_scope"])\n',
    "serialize": 'def f(instance):\n    return {"clock_scope": list(instance.scope)}\n',
    "serialize_json": "def f(clock_scope):\n    return json.dumps(list(clock_scope))\n",
    "carry": "def f(instance):\n    return AlignedEpisode(clock_scope=instance.scope)\n",
    "log": 'def f(clock_scope):\n    log.info("scope %s", clock_scope)\n',
    "column_select": 'def f(df):\n    return df.select("clock_scope")\n',
    "column_join": 'def f(df):\n    return df.with_columns(pl.col("clock_scope").list.join("/"))\n',
    "polars_other_column": 'def f(df):\n    return df.select(pl.col("observed_channels").list.get(0))\n',
    "sql_whole": 'def f(con):\n    return con.execute("SELECT * FROM a JOIN b ON a.clock_scope = b.clock_scope")\n',
    "docstring": 'def f():\n    """Never read clock_scope[0]."""\n',
    "other_scope_attribute": 'def f(request):\n    return request.scope["path"]\n',
    "args_scope": "def f(args):\n    return args.scope[0]\n",
    "bare_scope_name": "def f(scope):\n    return scope[0]\n",
    "row_of_other_column": 'def f(row):\n    return row["source_clock"][0]\n',
    "reassign_other": "def f(instance):\n    a, b = 1, 2\n    return a + b\n",
    "iterate_other": "def f(items):\n    for x in items:\n        pass\n",
}


def _scope_violations(tmp_path: Path, source: str, **tables) -> list[str]:
    root = _repo(tmp_path, {SCOPE_MUTATION: source})
    return [v for v in _isolated(root, scope_owners={}, **tables) if "clock scope" in v]


@pytest.mark.parametrize("form", SCOPE_READS)
def test_reading_a_scope_element_is_rejected(tmp_path, form):
    violations = _scope_violations(tmp_path, SCOPE_READS[form])
    assert len(violations) >= 1, SCOPE_READS[form]
    assert all("a clock scope is opaque" in v for v in violations)


@pytest.mark.parametrize("form", SCOPE_ALLOWED)
def test_carrying_or_comparing_a_whole_scope_is_allowed(tmp_path, form):
    assert _scope_violations(tmp_path, SCOPE_ALLOWED[form]) == [], SCOPE_ALLOWED[form]


def test_a_scope_read_is_reported_where_it_is(tmp_path):
    source = "def outer(clock_scope):\n\n    return clock_scope[0]\n"
    (violation,) = _scope_violations(tmp_path, source)
    assert f"{SCOPE_MUTATION}:3 (outer): subscripts a clock scope" in violation


def test_a_scope_owner_may_validate_the_shape_and_no_one_else(tmp_path):
    read = "def f(clock_scope):\n    return clock_scope[0]\n"
    owner = "packages/sceneops-core/sceneops_core/temporal/shape.py"
    root = _repo(tmp_path, {owner: read, CONSUMER: read})
    violations = _isolated(root, scope_owners=dispatch.SCOPE_OWNERS)
    assert len(violations) == 1 and violations[0].startswith(CONSUMER)


def test_the_rule_holds_in_source_aware_modules_too(tmp_path):
    """Registration may dispatch on a source; it has the binding and no use for a
    scope's elements."""
    module = "apps/worker/sceneops_worker/scenes/registration.py"
    source = (
        "def f(record, clock_scope):\n"
        '    if record.source_kind == "recording":\n'
        "        pass\n"
    )
    root = _repo(tmp_path, {module: source})
    assert _isolated(root, upstream=dispatch.UPSTREAM) == []
    root = _repo(tmp_path, {module: source + "    return clock_scope[0]\n"})
    (violation,) = _isolated(root, upstream=dispatch.UPSTREAM)
    assert "a clock scope is opaque" in violation


PROTECTED_SCOPE_MODULES = (
    "packages/sceneops-analytics/sceneops_analytics/tables.py",
    "packages/sceneops-analytics/sceneops_analytics/learning_tables.py",
    "packages/sceneops-analytics/sceneops_analytics/learning_dataset/dataset.py",
    "packages/sceneops-core/sceneops_core/episodes/alignment/schemas.py",
    "packages/sceneops-core/sceneops_core/episodes/alignment/engine.py",
    "packages/sceneops-core/sceneops_core/temporal/clocks.py",
    "packages/sceneops-core/sceneops_core/provenance/sources.py",
    ALIGN_JOB,
    SCENE_REGISTRATION,
)


@pytest.mark.parametrize("module", PROTECTED_SCOPE_MODULES)
def test_real_modules_read_no_scope_element(tmp_path, module):
    root = _repo(tmp_path, {module: (REPO_ROOT / module).read_text()})
    assert [
        site
        for site in dispatch.sites(root / module, module)
        if site.rule == dispatch.SCOPE_OPACITY
    ] == []


@pytest.mark.parametrize(
    "module",
    [m for m in PROTECTED_SCOPE_MODULES if not m.endswith(("clocks.py", "sources.py"))],
)
def test_a_scope_read_injected_into_a_real_module_is_rejected(tmp_path, module):
    source = (REPO_ROOT / module).read_text()
    injected = (
        source
        + "\n\ndef _injected(instance):\n    kind, _ = instance.scope\n    return kind\n"
    )
    root = _repo(tmp_path, {module: injected})
    violations = [
        v
        for v in _isolated(
            root, scope_owners=dispatch.SCOPE_OWNERS, upstream=dispatch.UPSTREAM
        )
        if "a clock scope is opaque" in v
    ]
    assert len(violations) == 1 and "(_injected)" in violations[0]


@pytest.mark.parametrize("module", ["clocks.py", "sources.py"])
def test_an_owner_module_is_exempt_only_under_its_own_path(tmp_path, module):
    owner = next(m for m in PROTECTED_SCOPE_MODULES if m.endswith(module))
    read = (
        REPO_ROOT / owner
    ).read_text() + "\n\ndef _shape(clock_scope):\n    return clock_scope[0]\n"
    root = _repo(tmp_path, {owner: read})
    assert not [
        v
        for v in _isolated(root, scope_owners=dispatch.SCOPE_OWNERS)
        if "clock scope" in v
    ]
    moved = f"packages/sceneops-x/sceneops_x/{module}"
    root = _repo(tmp_path / "moved", {moved: read})
    assert [
        v
        for v in _isolated(root, scope_owners=dispatch.SCOPE_OWNERS)
        if "clock scope" in v
    ]


def test_the_repository_reads_no_scope_element_outside_its_owners():
    assert [v for v in dispatch.check() if "clock scope" in v] == []
    # Nothing relies on an exception today: the owners are allowed to, and do not.
    reads = []
    for path, module in dispatch.production_modules(REPO_ROOT):
        reads += [
            s for s in dispatch.sites(path, module) if s.rule == dispatch.SCOPE_OPACITY
        ]
    assert reads == []


@pytest.mark.parametrize(
    "indirect",
    [
        'def f(row, column="clock_scope"):\n    return row[column][0]\n',
        "def f(obj):\n    return obj.get_scope()[0]\n",
        "def f(self):\n    self._s = self.clock_scope\n    return self._s[0]\n",
        'def f(obj):\n    return getattr(obj, "clock_scope")[0]\n',
        "def f(clock_scope):\n    return len(clock_scope) == 2\n",
        'def f(rows):\n    return [r["clock_scope"] for r in rows][0][0]\n',
    ],
)
def test_the_rule_is_a_guard_against_direct_forms_not_a_dataflow_analysis(
    tmp_path, indirect
):
    """Stated so the limit stays visible: these are not detected. ADR-021's manifest
    comparison does not depend on this rule."""
    assert _scope_violations(tmp_path, indirect) == []
