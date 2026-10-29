"""The independent native-label verifier (tools/reference/verification/nuscenes_labels.py).

The verifier imports no SceneOps package; these tests do, to hold the platform's
native label importer, Scene builder and sample views to the verifier's
independently implemented rules on synthetic v1.0 trees and on the raw
scene-0061 slice (skipped without data/raw/nuscenes), and to prove that every
negative control is detected. No test reads or writes platform state.
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

from verification import nuscenes_labels, nuscenes_scene, rules  # noqa: E402
from verification.findings import Findings  # noqa: E402

from sceneops_core.common.derived_ids import label_set_artifact_id  # noqa: E402
from sceneops_core.labels import LabelSetRef  # noqa: E402
from sceneops_core.provenance import PinnedContent  # noqa: E402
from sceneops_core.sample_views import (  # noqa: E402
    MemberPolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
    SceneRevisionRef,
    build_scene_sample_view,
)
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig  # noqa: E402
from sceneops_core.sources import SnapshotSelection  # noqa: E402
from sceneops_derived.nuscenes_labels import nuscenes_label_set  # noqa: E402
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes  # noqa: E402
from sceneops_sources import LocalContentFiles, build_inventory, select_files  # noqa: E402
from sceneops_sources.nuscenes_synthetic import (  # noqa: E402
    CHANNELS,
    INCLUDE,
    token,
    write_nuscenes_tree,
)

LABEL_SET_ID = "gt-native"


def _platform_observation(root: Path, include, channels, unit_key, expected):
    """The state a platform import of ``root`` produces: the label set, the
    Scene manifest and both views are the platform code's own bytes; the
    records around them are the synthetic state's (pure code writes no
    record)."""
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=tuple(include)))
    )
    pinned, files = PinnedContent(inventory=inventory), LocalContentFiles(root, inventory)
    labels = nuscenes_label_set(
        pinned=pinned, files=files, label_set_id=LABEL_SET_ID, unit_keys=[unit_key]
    )
    (planned,) = plan_nuscenes_scenes(
        pinned=pinned,
        files=files,
        config=NuScenesSceneBuildConfig(channels=channels),
        unit_keys=[unit_key],
    ).scenes
    scene = planned.manifest
    scene_id = "scene-" + "a" * 32
    obs = nuscenes_labels.synthetic_observation(
        expected, json.loads(scene.to_canonical_bytes()), scene_id=scene_id
    )
    data = labels.to_canonical_bytes()
    obs["label_set"]["bytes"] = data.decode("utf-8")
    checksum = labels.checksum()
    ref = LabelSetRef(
        label_set_id=LABEL_SET_ID,
        manifest_artifact_id=label_set_artifact_id(label_set_id=LABEL_SET_ID, checksum=checksum),
        manifest_checksum=checksum,
    )
    obs["label_set"]["artifact"].update(
        artifact_id=ref.manifest_artifact_id, checksum=checksum, size_bytes=len(data)
    )
    revision = obs["scene"]["revisions"][0]
    obs["views"] = []
    for anchor, member in (("CAM_FRONT", "LIDAR_TOP"), ("LIDAR_TOP", "CAM_FRONT")):
        view = build_scene_sample_view(
            scene=SceneRevisionRef(
                scene_id=scene_id,
                manifest_artifact_id=revision["manifest_artifact_id"],
                manifest_checksum=revision["manifest_checksum"],
            ),
            manifest=scene,
            policy=SampleViewPolicy(
                anchor=SampleAnchorPolicy(channel=anchor),
                members=[MemberPolicy(channel=member, tolerance_ns=50_000_000, required=False)],
            ),
            label_sets=[(ref, labels)],
        )
        obs["views"].append(
            {
                "artifact": {"artifact_id": f"sampleview-{anchor}", "checksum": view.checksum()},
                "bytes": view.to_canonical_bytes().decode("utf-8"),
            }
        )
    return obs


def _verify(obs, expected) -> Findings:
    f = Findings()
    nuscenes_labels.verify(f, obs, expected)
    return f


def _synthetic(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=2, samples=4, sweeps=1)
    raw = nuscenes_scene.read_raw(root, INCLUDE)
    unit_key = token("scene", 1)
    expected = nuscenes_labels.derive(raw, label_set_id=LABEL_SET_ID, unit_keys=[unit_key])
    return root, raw, unit_key, expected


def test_the_platform_satisfies_the_independent_rules(tmp_path):
    root, _, unit_key, expected = _synthetic(tmp_path)
    obs = _platform_observation(root, INCLUDE, sorted(CHANNELS), unit_key, expected)
    findings = _verify(obs, expected)
    assert findings.ok, findings.violations[:5]
    assert obs["label_set"]["artifact"]["checksum"] == expected.checksum
    assert len(expected.document["coverage"]) == 4
    # The last sample of the scene is covered and annotated as empty.
    assert sorted(len(v) for v in expected.by_group.values()) == [0, 2, 2, 2]


def test_every_negative_control_is_detected(tmp_path):
    root, _, unit_key, expected = _synthetic(tmp_path)
    obs = _platform_observation(root, INCLUDE, sorted(CHANNELS), unit_key, expected)
    results = nuscenes_labels.run_controls(obs, lambda c: _verify(c, expected).codes())
    assert {r["control"] for r in results if not r["detected"]} == set()
    assert len(results) == len(nuscenes_labels.controls())


def test_a_sample_off_its_key_frame_is_a_raw_refusal(tmp_path):
    def shift(tables):
        tables["sample"][-1]["timestamp"] += 1

    root = write_nuscenes_tree(tmp_path, mutate_tables=shift)
    raw = nuscenes_scene.read_raw(root, INCLUDE)
    expected = nuscenes_labels.derive(
        raw, label_set_id=LABEL_SET_ID, unit_keys=[token("scene", 1)]
    )
    assert expected.refusals
    f = Findings()
    nuscenes_labels.verify(f, nuscenes_labels.empty_observation(), expected)
    assert "raw_label_rules" in f.codes()


def test_the_artifact_id_formula_matches_the_documented_one():
    checksum = rules.checksum(b"x")
    assert nuscenes_labels.derived_artifact_id(
        prefix="labelset", logical_id="gt", checksum=checksum
    ) == label_set_artifact_id(label_set_id="gt", checksum=checksum)


def test_the_verifier_imports_no_sceneops_package():
    imported = set()
    for path in (
        TOOLS / "verification/nuscenes_labels.py",
        TOOLS / "nuscenes_label_verify.py",
    ):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert {name for name in imported if name not in sys.stdlib_module_names} <= {
        "verification"
    }


def test_the_platform_satisfies_the_rules_on_scene_0061():
    root = REPO_ROOT / "data/raw/nuscenes"
    if not (root / "v1.0-mini" / "scene.json").is_file():
        pytest.skip("data/raw/nuscenes is missing")
    include = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"]["nuscenes-scene-0061"]["include"]
    channels = json.loads(
        (REPO_ROOT / "config/reference/nuscenes-scene-0061/scene_build_config.json").read_text()
    )["channels"]
    unit_key = "cc8c0bf57f984915a77078b10eb33198"
    raw = nuscenes_scene.read_raw(root, include)
    expected = nuscenes_labels.derive(raw, label_set_id=LABEL_SET_ID, unit_keys=[unit_key])
    assert (len(expected.document["coverage"]), len(expected.document["labels"])) == (39, 4699)
    obs = _platform_observation(root, include, channels, unit_key, expected)
    findings = _verify(obs, expected)
    assert findings.ok, findings.violations[:5]
