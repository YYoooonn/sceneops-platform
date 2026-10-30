"""The independent LeRobot Episode verifier (tools/reference/verification/lerobot.py).

The verifier imports no SceneOps package; these tests do, to hold the platform's
LeRobot builder to the verifier's independently implemented rules on synthetic
v3.0 trees (both ``names`` forms and both list types), and to prove that every
negative control is detected. No test reads or writes platform state.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from verification import lerobot, rules  # noqa: E402
from verification.findings import Findings  # noqa: E402

from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig  # noqa: E402
from sceneops_core.provenance import PinnedContent  # noqa: E402
from sceneops_core.sources import SnapshotSelection  # noqa: E402
from sceneops_episodes.lerobot_builder import plan_lerobot_episodes  # noqa: E402
from sceneops_sources import LocalContentFiles, build_inventory, select_files  # noqa: E402
from sceneops_sources.lerobot_synthetic import (  # noqa: E402
    INCLUDE,
    float32,
    write_lerobot_tree,
)

CONFIG = json.loads(
    (
        TOOLS.parents[1] / "config/reference/lerobot-pusht/episode_build_config.json"
    ).read_text()
)
DECLARATION = {
    "format": "lerobot",
    "upstream": "test/synthetic",
    "upstream_revision": "r1",
    "annotations": {"license": "mit"},
}
SNAPSHOT = {
    "snapshot_id": "snapshot-" + "1" * 32,
    **{k: v for k, v in DECLARATION.items() if k != "annotations"},
}


def _platform_observation(root: Path, raw, expected) -> dict:
    """The state a platform build of ``root`` produces: the manifests are the
    platform builder's own bytes; the records around them are the synthetic
    state's (the builder writes no record)."""
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=INCLUDE))
    )
    plan = plan_lerobot_episodes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(root, inventory),
        config=LeRobotEpisodeBuildConfig.model_validate(CONFIG),
    )
    obs = lerobot.synthetic_observation(
        raw, expected, snapshot=SNAPSHOT, dataset_key="ds/v1"
    )
    built = {
        rules.unit_id(
            domain="episode",
            source_kind="content",
            source_id=inventory.content_id,
            unit_key=e.unit_key,
        ): e.manifest
        for e in plan.episodes
    }
    assert set(built) == set(obs["units"])
    for unit_id, manifest in built.items():
        document = json.loads(manifest.to_canonical_bytes())
        lerobot.rewrite_unit(
            obs, unit_id, lambda m, d=document: (m.clear(), m.update(d))
        )
    return obs


@pytest.mark.parametrize("names_form", ["dict", "list"])
@pytest.mark.parametrize("fixed", [True, False])
def test_the_platform_builder_satisfies_the_independent_rules(
    tmp_path, names_form, fixed
):
    root = write_lerobot_tree(
        tmp_path, lengths=(3, 1, 4), fps=30, names_form=names_form, fixed_size_lists=fixed,
        tasks=("reach", "grasp"), task_index=lambda e, k: 1 if k >= 2 else 0,
    )  # fmt: skip
    raw = lerobot.read_raw(root, INCLUDE)
    expected = lerobot.derive(raw, CONFIG)
    obs = _platform_observation(root, raw, expected)
    findings = Findings()
    lerobot.verify(
        findings, obs, raw, expected, declaration=DECLARATION, all_units=True
    )
    assert findings.ok, findings.violations[:5]


def test_every_negative_control_is_detected(tmp_path):
    root = write_lerobot_tree(tmp_path, lengths=(3, 2))
    raw = lerobot.read_raw(root, INCLUDE)
    expected = lerobot.derive(raw, CONFIG)
    obs = lerobot.synthetic_observation(
        raw, expected, snapshot=SNAPSHOT, dataset_key="ds/v1"
    )

    def codes(corrupted):
        f = Findings()
        lerobot.verify(
            f, corrupted, raw, expected, declaration=DECLARATION, all_units=True
        )
        return f.codes()

    assert codes(obs) == set()
    results = lerobot.run_controls(obs, codes)
    assert [r["control"] for r in results if not r["detected"]] == []
    assert len(results) == 11


def test_a_raw_tree_off_its_frame_grid_expects_no_build(tmp_path):
    root = write_lerobot_tree(
        tmp_path,
        lengths=(3,),
        timestamp=lambda e, k: float32(k / 10 + (1e-5 if k == 2 else 0)),
    )
    raw = lerobot.read_raw(root, INCLUDE)
    (unit,) = lerobot.derive(raw, CONFIG).values()
    assert unit.refusals and "frame 2" in unit.refusals[0]


def test_frame_time_and_selectors_follow_the_documented_rounding():
    assert lerobot.frame_time(1, 30) == 33_333_333
    assert lerobot.frame_time(2, 30) == 66_666_667
    assert lerobot.frame_time(161, 10) == 16_100_000_000


def test_the_verifier_imports_no_sceneops_package():
    imported = set()
    for path in (TOOLS / "verification/lerobot.py", TOOLS / "lerobot_verify.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert not {name for name in imported if name.startswith("sceneops")}
