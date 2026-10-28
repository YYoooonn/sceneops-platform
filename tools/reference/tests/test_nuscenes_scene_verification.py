"""The independent nuScenes Scene verifier (tools/reference/verification/nuscenes_scene.py).

The verifier imports no SceneOps package; these tests do, to hold the platform's
nuScenes builder to the verifier's independently implemented rules on synthetic
v1.0 trees and on the raw scene-0061 slice (skipped without data/raw/nuscenes),
and to prove that every negative control is detected. No test reads or writes
platform state.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from verification import nuscenes_scene, rules  # noqa: E402
from verification.findings import Findings  # noqa: E402

from sceneops_core.provenance import PinnedContent  # noqa: E402
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig  # noqa: E402
from sceneops_core.sources import SnapshotSelection  # noqa: E402
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes  # noqa: E402
from sceneops_sources import LocalContentFiles, build_inventory, select_files  # noqa: E402
from sceneops_sources.nuscenes_synthetic import (  # noqa: E402
    CHANNELS,
    INCLUDE,
    token,
    write_nuscenes_tree,
)

CONFIG = {"channels": sorted(CHANNELS)}
DECLARATION = {
    "format": "nuscenes",
    "upstream": "test/synthetic",
    "upstream_revision": "r1",
    "annotations": {"license": "nuscenes-terms-of-use"},
}
SNAPSHOT = {
    "snapshot_id": "snapshot-" + "1" * 32,
    **{k: v for k, v in DECLARATION.items() if k != "annotations"},
}


def _platform_observation(root: Path, include, config, raw, expected, unit_keys=None):
    """The state a platform build of ``root`` produces: the manifests are the
    platform builder's own bytes; the records around them are the synthetic
    state's (the builder writes no record)."""
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=tuple(include)))
    )
    plan = plan_nuscenes_scenes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(root, inventory),
        config=NuScenesSceneBuildConfig.model_validate(config),
        unit_keys=unit_keys,
    )
    obs = nuscenes_scene.synthetic_observation(
        raw, expected, snapshot=SNAPSHOT, dataset_key="ds/v1"
    )
    built = {
        rules.unit_id(
            domain="scene",
            source_kind="content",
            source_id=inventory.content_id,
            unit_key=s.unit_key,
        ): s.manifest
        for s in plan.scenes
    }
    assert set(built) == set(obs["units"])
    for unit_id, manifest in built.items():
        document = json.loads(manifest.to_canonical_bytes())
        nuscenes_scene.rewrite_unit(
            obs, unit_id, lambda m, d=document: (m.clear(), m.update(d))
        )
    return obs


def _verify(obs, raw, expected, *, all_units=True) -> Findings:
    findings = Findings()
    nuscenes_scene.verify(
        findings, obs, raw, expected, declaration=DECLARATION, all_units=all_units
    )
    return findings


@pytest.mark.parametrize("channels", [sorted(CHANNELS), ["LIDAR_TOP", "RADAR_FRONT"]])
def test_the_platform_builder_satisfies_the_independent_rules(tmp_path, channels):
    root = write_nuscenes_tree(tmp_path, scenes=2, samples=3, sweeps=2)
    config = {"channels": channels}
    raw = nuscenes_scene.read_raw(root, INCLUDE)
    expected = nuscenes_scene.derive(raw, config)
    obs = _platform_observation(root, INCLUDE, config, raw, expected)
    findings = _verify(obs, raw, expected)
    assert findings.ok, findings.violations[:5]


def test_a_scene_whose_files_are_not_preserved_expects_no_build(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=2, omit_scene_files=(0,))
    raw = nuscenes_scene.read_raw(root, INCLUDE)
    expected = nuscenes_scene.derive(raw, CONFIG)
    refused = {u.unit_key for u in expected.values() if u.refusals}
    assert refused == {token("scene", 0)}
    obs = _platform_observation(
        root, INCLUDE, CONFIG, raw, expected, unit_keys=[token("scene", 1)]
    )
    assert _verify(obs, raw, expected).ok


def test_every_negative_control_is_detected(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=1, samples=3, sweeps=1)
    raw = nuscenes_scene.read_raw(root, INCLUDE)
    expected = nuscenes_scene.derive(raw, CONFIG)
    obs = nuscenes_scene.synthetic_observation(
        raw, expected, snapshot=SNAPSHOT, dataset_key="ds/v1"
    )
    assert _verify(obs, raw, expected).codes() == set()
    results = nuscenes_scene.run_controls(
        obs, lambda corrupted: _verify(corrupted, raw, expected).codes()
    )
    assert [r["control"] for r in results if not r["detected"]] == []
    assert len(results) == 16


def test_a_built_refused_scene_is_a_violation(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=2, omit_scene_files=(0,))
    raw = nuscenes_scene.read_raw(root, INCLUDE)
    expected = nuscenes_scene.derive(raw, CONFIG)
    obs = nuscenes_scene.synthetic_observation(raw, expected, snapshot=SNAPSHOT)
    refused = next(u for u in expected.values() if u.refusals)
    obs["units"][refused.unit_id] = {"record": {}, "revisions": []}
    assert "refused_unit" in _verify(obs, raw, expected).codes()


def test_the_verifier_imports_no_sceneops_package_and_no_third_party_reader():
    imported = set()
    for path in (
        TOOLS / "verification/nuscenes_scene.py",
        TOOLS / "verification/nuscenes_source.py",
        TOOLS / "nuscenes_scene_verify.py",
    ):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert {name for name in imported if name not in sys.stdlib_module_names} <= {
        "verification"
    }


# --- the raw scene-0061 slice ------------------------------------------------------


@pytest.fixture(scope="module")
def raw_slice():
    root = REPO_ROOT / "data/raw/nuscenes"
    if not (root / "v1.0-mini" / "scene.json").is_file():
        pytest.skip("data/raw/nuscenes is missing")
    declaration = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"]["nuscenes-scene-0061"]
    config = json.loads(
        (
            REPO_ROOT / "config/reference/nuscenes-scene-0061/scene_build_config.json"
        ).read_text()
    )
    raw = nuscenes_scene.read_raw(root, declaration["include"])
    return root, declaration, config, raw


def test_the_platform_builder_satisfies_the_rules_on_scene_0061(raw_slice):
    root, declaration, config, raw = raw_slice
    scene_0061 = "cc8c0bf57f984915a77078b10eb33198"
    expected = nuscenes_scene.derive(raw, config)
    assert {u.unit_key for u in expected.values() if not u.refusals} == {scene_0061}
    obs = _platform_observation(
        root, declaration["include"], config, raw, expected, unit_keys=[scene_0061]
    )
    findings = Findings()
    nuscenes_scene.verify(
        findings,
        obs,
        raw,
        expected,
        declaration={**declaration, "upstream": "test/synthetic"},
        all_units=True,
    )
    assert [v for v in findings.violations if v["code"] != "snapshot"] == []
