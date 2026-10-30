"""The mixed-source DatasetVersion verifier (tools/reference/verification/mixed_source.py).

The verifier imports no SceneOps package; these tests do, to hold the platform's
content builders to the pins the verifier derives from the raw trees, and to prove
that the committed expectations are the derivation and that every negative control
is detected. They need the locked scene-0061 recording, the raw nuScenes tree and
the pinned pusht tree, and skip without them. No test reads or writes platform
state.
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from verification import mixed_source, rules  # noqa: E402
from verification.findings import Findings  # noqa: E402

from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig  # noqa: E402
from sceneops_core.provenance import PinnedContent  # noqa: E402
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig  # noqa: E402
from sceneops_core.sources import SnapshotSelection  # noqa: E402
from sceneops_episodes.lerobot_builder import plan_lerobot_episodes  # noqa: E402
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes  # noqa: E402
from sceneops_sources import LocalContentFiles, build_inventory, select_files  # noqa: E402

CACHE_ROOT = REPO_ROOT / "data/reference"


def _inputs_present() -> bool:
    snapshots = json.loads(mixed_source.SNAPSHOTS_PATH.read_text())["snapshots"]
    return (
        (REPO_ROOT / snapshots["nuscenes-scene-0061"]["local_root"] / "v1.0-mini").is_dir()
        and (REPO_ROOT / snapshots["lerobot-pusht"]["local_root"] / "meta").is_dir()
        and any((CACHE_ROOT / "nuscenes-mini-v1" / "recordings").glob("scene-0061-*.mcap"))
    )


pytestmark = pytest.mark.skipif(
    not _inputs_present(),
    reason="needs data/raw/nuscenes, data/raw/lerobot/pusht and the locked scene-0061 recording",
)


def _small_spec() -> dict:
    """The committed spec with two pusht episodes, so the controls run fast."""
    spec = copy.deepcopy(mixed_source.load_spec())
    for part in spec["parts"]:
        if part["part"] == "lerobot-pusht":
            part["unit_keys"] = ["0", "1"]
    return spec


@pytest.fixture(scope="module")
def small():
    return mixed_source.derive(_small_spec(), cache_root=CACHE_ROOT)


def _verify(obs, expected) -> Findings:
    findings = Findings()
    mixed_source.verify(findings, obs, expected)
    return findings


def test_the_verifier_imports_no_sceneops_package():
    imported = set()
    for path in (TOOLS / "verification/mixed_source.py", TOOLS / "mixed_source_verify.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert {name for name in imported if name not in sys.stdlib_module_names} <= {
        "verification"
    }


def test_the_committed_expectations_are_the_derivation():
    expected = mixed_source.derive(mixed_source.load_spec(), cache_root=CACHE_ROOT)
    findings = Findings()
    mixed_source.check_expectations(
        findings,
        json.loads(mixed_source.EXPECTATIONS_PATH.read_text()),
        mixed_source.expectations_document(expected),
    )
    assert findings.violations == []
    summary = expected.summary()
    assert (summary["scene_count"], summary["keyframe_count"], summary["episode_count"]) == (2, 39, 207)
    kinds = {(m.domain, m.source_kind) for m in expected.members.values()}
    assert kinds == {("scene", "recording"), ("episode", "recording"), ("scene", "content"), ("episode", "content")}


def _built_manifests(part: str) -> dict[str, bytes]:
    """The platform builder's manifest bytes of one content part, by unit id."""
    spec = next(p for p in _small_spec()["parts"] if p["part"] == part)
    declaration = json.loads(mixed_source.SNAPSHOTS_PATH.read_text())["snapshots"][spec["snapshot"]]
    root = REPO_ROOT / declaration["local_root"]
    inventory = build_inventory(root, select_files(root, SnapshotSelection(include=tuple(declaration["include"]))))
    config = json.loads((REPO_ROOT / spec["build_config"]).read_text())
    common = {"pinned": PinnedContent(inventory=inventory), "files": LocalContentFiles(root, inventory), "unit_keys": spec["unit_keys"]}
    if declaration["format"] == "nuscenes":
        plan = plan_nuscenes_scenes(config=NuScenesSceneBuildConfig.model_validate(config), **common)
        units = [(s.manifest, "scene") for s in plan.scenes]
    else:
        plan = plan_lerobot_episodes(config=LeRobotEpisodeBuildConfig.model_validate(config), **common)
        units = [(e.manifest, "episode") for e in plan.episodes]
    return {
        rules.unit_id(domain=domain, source_kind="content", source_id=inventory.content_id,
                      unit_key=m.lineage.source.unit_key): m.to_canonical_bytes()
        for m, domain in units
    }  # fmt: skip


@pytest.mark.parametrize("part", ["nuscenes-scene-0061", "lerobot-pusht"])
def test_the_platform_builders_produce_the_derived_pins(small, part):
    """The content pins are derived whole: the builder's bytes hash to them."""
    built = _built_manifests(part)
    derived = {u: m.manifest_checksum for u, m in small.members.items() if m.part == part}
    assert {u: rules.checksum(b) for u, b in built.items()} == derived


@pytest.mark.parametrize("with_selector", [False, True])
def test_a_synthetic_composition_passes_and_every_control_is_detected(small, with_selector):
    obs = mixed_source.synthetic_observation(small, dataset_key="ds/mixed", with_selector=with_selector)
    assert _verify(obs, small).violations == []
    results = mixed_source.run_controls(obs, lambda corrupted: _verify(corrupted, small).codes())
    assert [r["control"] for r in results if not r["detected"]] == []
    assert ("selector_clause" in {r["control"] for r in results}) is with_selector


def test_a_version_name_that_is_also_a_manifest_key_is_not_membership(small):
    """The LeRobot Episodes' PayloadRefs carry a "selector" key; a version named
    `selector` is not written into them. The name as a value still is."""
    obs = mixed_source.synthetic_observation(small, dataset_key="ds/selector", with_selector=True)
    assert any('"selector":' in e["bytes"] for e in obs["manifests"].values())
    assert _verify(obs, small).violations == []
    results = mixed_source.run_controls(obs, lambda corrupted: _verify(corrupted, small).codes())
    assert [r["control"] for r in results if not r["detected"]] == []


def test_the_composition_must_name_every_part(small):
    """A version of the content parts only is not the mixed composition."""
    obs = mixed_source.synthetic_observation(small, dataset_key="ds/mixed", with_selector=False)
    version = obs["dataset_version"]
    version["members"] = [
        m for m in version["members"]
        if obs["units"][m["domain"]][m["unit_id"]]["record"]["source_kind"] == "content"
    ]  # fmt: skip
    version["version"]["composition_checksum"] = rules.composition_checksum(
        (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in version["members"]
    )
    assert {"composition_members", "composition_checksum"} <= _verify(obs, small).codes()
