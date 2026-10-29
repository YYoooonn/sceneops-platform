"""The source-dispatch check rejects what it claims to reject, and the repository
satisfies it (ADR-015 MS-10)."""

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


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


def test_the_repository_dispatches_on_source_only_where_allowed():
    assert dispatch.check() == []


@pytest.mark.parametrize(
    "body",
    [
        "def f(m):\n    return m.lineage.source.source_kind == 'content'\n",
        "def f(record):\n    if record.source_kind != 'recording':\n        return 1\n",
        "def f(unit):\n    return ('recording', unit.robot_run_id) == unit.key\n",
        "def f(ref):\n    return isinstance(ref, SourceFileRef)\n",
        "def f(ref):\n    return isinstance(ref, (ArtifactPayloadRef, int))\n",
        "def f(src):\n    match src:\n        case ContentUnitSource():\n            return 1\n",
        "def f(ref):\n    return ref.kind == 'source_file'\n",
        "def f(snapshot):\n    return snapshot.format == 'nuscenes'\n",
        "READERS = {'lerobot': 1, 'nuscenes': 2}\n",
    ],
)
def test_a_downstream_dispatch_site_is_rejected(tmp_path, body):
    root = _repo(tmp_path, {CONSUMER: body})
    (violation,) = dispatch.check(root, upstream={}, downstream={}, identity={})
    assert violation.startswith(CONSUMER)


def test_ordinary_code_is_not_a_dispatch_site(tmp_path):
    body = (
        "def f(store, clause, ref):\n"
        "    rows = store.list(source_kind=clause.source_kind, source_id=clause.source_id)\n"
        "    return rows, ref.media_type == 'image/jpeg', ref.kind\n"
    )
    root = _repo(tmp_path, {CONSUMER: body})
    assert dispatch.check(root, upstream={}, downstream={}, identity={}) == []


IDENTITY_BODY = (
    "class R:\n"
    "    def resolve(self, anchor):\n"
    "        return (anchor.source_kind, anchor.source_id) != self.source\n"
)


def test_an_identity_match_is_allowed_only_at_the_anchor_resolver(tmp_path):
    root = _repo(tmp_path, {CONSUMER: IDENTITY_BODY})
    assert (
        dispatch.check(
            root, upstream={}, downstream={}, identity={(CONSUMER, "R.resolve"): "x"}
        )
        == []
    )
    # The same comparison in any other derived workflow is a violation.
    (violation,) = dispatch.check(root, upstream={}, downstream={}, identity={})
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
    violations = dispatch.check(
        root, upstream={}, downstream={}, identity={(CONSUMER, "R.resolve"): "x"}
    )
    assert violations and all("anchor resolver" in v for v in violations)


def test_a_listed_identity_site_that_no_longer_exists_is_reported(tmp_path):
    root = _repo(tmp_path, {CONSUMER: "def f():\n    return 1\n"})
    (violation,) = dispatch.check(
        root, upstream={}, downstream={}, identity={(CONSUMER, "f"): "x"}
    )
    assert "remove it from IDENTITY" in violation


def test_upstream_modules_and_listed_sites_are_allowed(tmp_path):
    root = _repo(
        tmp_path,
        {
            "packages/sceneops-up/sceneops_up/builder.py": "X = {'nuscenes': 1}\n",
            CONSUMER: "class L:\n    def uris(self, r):\n        return isinstance(r, SourceFileRef)\n",
            "packages/sceneops-x/tests/test_consumer.py": "Y = {'lerobot': 1}\n",
        },
    )
    assert (
        dispatch.check(
            root,
            upstream={"packages/sceneops-up/": "a builder"},
            downstream={(CONSUMER, "L.uris"): "the locator"},
            identity={},
        )
        == []
    )


def test_a_listed_site_that_no_longer_exists_is_reported(tmp_path):
    root = _repo(tmp_path, {CONSUMER: "def f():\n    return 1\n"})
    (violation,) = dispatch.check(
        root, upstream={}, downstream={(CONSUMER, "f"): "was a refusal"}, identity={}
    )
    assert "no such site exists" in violation


def test_a_site_moved_to_another_function_is_not_covered_by_the_listing(tmp_path):
    body = "def g(ref):\n    return isinstance(ref, SourceFileRef)\n"
    root = _repo(tmp_path, {CONSUMER: body})
    violations = dispatch.check(
        root, upstream={}, downstream={(CONSUMER, "f"): "x"}, identity={}
    )
    assert len(violations) == 2
