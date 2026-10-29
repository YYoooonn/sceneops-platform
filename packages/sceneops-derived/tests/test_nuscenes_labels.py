"""Native nuScenes labels (ADR-015 §9, M3.0 OQ-6).

Synthetic v1.0 trees exercise each rule and each refusal; expectations are
the values the tree was written with. The raw ``scene-0061`` slice is held to
the numbers of the M3 plan (S6 acceptance), measured on the raw data in M3.0;
that test skips without data/raw/nuscenes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sceneops_core.labels import LabelSourceKind, load_canonical_label_set
from sceneops_core.provenance import PinnedContent
from sceneops_core.sources import SnapshotSelection
from sceneops_derived.nuscenes_labels import (
    ANCHOR_CHANNEL,
    ANCHOR_CLOCK,
    BOX_FRAME,
    NuScenesLabelImportError,
    nuscenes_label_set,
)
from sceneops_sources import LocalContentFiles, build_inventory, select_files
from sceneops_sources.nuscenes_synthetic import (
    BASE_US,
    INCLUDE,
    INSTANCES,
    KEY_FRAME_OFFSET_US,
    VERSION,
    token,
    write_nuscenes_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
NUSCENES = REPO_ROOT / "data/raw/nuscenes"
SCENE_0061 = "cc8c0bf57f984915a77078b10eb33198"


def _content(root: Path, include=INCLUDE) -> tuple[PinnedContent, LocalContentFiles]:
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=tuple(include)))
    )
    return PinnedContent(inventory=inventory), LocalContentFiles(root, inventory)


def _labels(root: Path, *, include=INCLUDE, unit_keys=None, label_set_id="gt"):
    pinned, files = _content(root, include)
    return pinned, nuscenes_label_set(
        pinned=pinned, files=files, label_set_id=label_set_id, unit_keys=unit_keys
    )


def test_one_anchor_per_sample_on_its_lidar_key_frame_with_the_sample_token(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=2, samples=3)
    pinned, labels = _labels(root)
    assert [
        (a.source_kind, a.source_id, a.channel, a.source_clock) for a in labels.coverage
    ] == [("content", pinned.content_id, ANCHOR_CHANNEL, ANCHOR_CLOCK)] * 6
    expected = sorted(
        ((BASE_US + s * 10**8 + k * 500_000) * 1000, token("sample", s, k))
        for s in range(2)
        for k in range(3)
    )
    assert [(a.timestamp_ns, a.group_ref) for a in labels.coverage] == expected
    assert KEY_FRAME_OFFSET_US[ANCHOR_CHANNEL] == 0
    # The last sample of each scene is covered and annotated as empty.
    labelled = {label.anchor.group_ref for label in labels.labels}
    assert labelled == {token("sample", s, k) for s in range(2) for k in range(2)}


def test_labels_keep_tokens_categories_instances_attributes_and_geometry(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=2, samples=3)
    _, labels = _labels(root)
    assert len(labels.labels) == 2 * 2 * len(INSTANCES)
    label = next(
        item
        for item in labels.labels
        if item.label_id == token("sample_annotation", 1, "vehicle.car", 1)
    )
    assert label.anchor.group_ref == token("sample", 1, 1)
    assert (label.category, label.instance_id, label.attributes) == (
        "vehicle.car",
        token("instance", 1, "vehicle.car"),
        ["vehicle.moving"],
    )
    assert label.box.frame_id == BOX_FRAME == "global"
    assert (label.box.center_m, label.box.size_wlh_m, label.box.rotation_wxyz) == (
        (416.0, 1104.5, 1.0),
        (1.8, 4.5, 1.5),
        (0.8, 0.0, 0.0, 0.6),
    )
    assert label.box.velocity_mps is None
    assert labels.provenance.kind == LabelSourceKind.EXTERNAL
    assert (labels.provenance.producer, labels.provenance.producer_version) == (
        "nuscenes",
        VERSION,
    )


def test_the_label_set_is_a_function_of_the_content_and_scope(tmp_path):
    root = write_nuscenes_tree(tmp_path / "a", scenes=2, samples=3)
    _, first = _labels(root)
    _, again = _labels(root)
    assert first.to_canonical_bytes() == again.to_canonical_bytes()
    assert load_canonical_label_set(first.to_canonical_bytes()) == first
    # A subset holds exactly that scene's anchors and labels.
    _, one = _labels(root, unit_keys=[token("scene", 1)])
    assert {a.group_ref for a in one.coverage} == {
        token("sample", 1, k) for k in range(3)
    }
    assert [label.model_dump() for label in one.labels] == [
        label.model_dump()
        for label in first.labels
        if label.anchor.group_ref in {a.group_ref for a in one.coverage}
    ]
    # The same bytes under another root are the same content and labels.
    other = write_nuscenes_tree(tmp_path / "b", scenes=2, samples=3)
    assert _labels(other)[1].to_canonical_bytes() == first.to_canonical_bytes()


def test_unknown_unit_keys_are_refused(tmp_path):
    root = write_nuscenes_tree(tmp_path)
    with pytest.raises(NuScenesLabelImportError, match="name no scene"):
        _labels(root, unit_keys=["f" * 32])


def _mutated(tmp_path, mutate):
    return write_nuscenes_tree(tmp_path, mutate_tables=mutate)


def test_a_sample_off_its_lidar_key_frame_is_refused(tmp_path):
    def shift(tables):
        tables["sample"][1]["timestamp"] += 1

    with pytest.raises(NuScenesLabelImportError, match="at the sample's own time"):
        _labels(_mutated(tmp_path, shift))


def test_a_sample_without_exactly_one_lidar_key_frame_is_refused(tmp_path):
    lidar = token("calibrated_sensor", 0, "LIDAR_TOP")

    def demote(tables):
        for row in tables["sample_data"]:
            if row["calibrated_sensor_token"] == lidar and row["is_key_frame"]:
                row["is_key_frame"] = False
                return

    with pytest.raises(NuScenesLabelImportError, match="0 LIDAR_TOP key frames"):
        _labels(_mutated(tmp_path, demote))


def test_an_annotation_of_an_unknown_sample_is_refused(tmp_path):
    def orphan(tables):
        tables["sample_annotation"][0]["sample_token"] = "f" * 32

    with pytest.raises(NuScenesLabelImportError, match="unknown samples"):
        _labels(_mutated(tmp_path, orphan))


def test_an_unrepresentable_box_is_refused(tmp_path):
    def flatten(tables):
        tables["sample_annotation"][0]["rotation"] = [0.5, 0.0, 0.0, 0.0]

    with pytest.raises(NuScenesLabelImportError, match="not representable"):
        _labels(_mutated(tmp_path, flatten))


def test_a_scene_whose_anchor_files_are_not_preserved_is_refused(tmp_path):
    root = write_nuscenes_tree(tmp_path, omit_scene_files=(1,))
    # The tables still describe scene 1; its key frame files are not content.
    with pytest.raises(NuScenesLabelImportError, match="is not a file of"):
        _labels(root)
    _, labels = _labels(root, unit_keys=[token("scene", 0)])
    assert {a.group_ref for a in labels.coverage} == {
        token("sample", 0, k) for k in range(3)
    }


# --- the raw scene-0061 slice ------------------------------------------------------------


def test_scene_0061_imports_4699_labels_over_39_samples():
    if not (NUSCENES / "v1.0-mini" / "scene.json").is_file():
        pytest.skip("data/raw/nuscenes is missing")
    snapshots = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"]
    pinned, labels = _labels(
        NUSCENES,
        include=snapshots["nuscenes-scene-0061"]["include"],
        unit_keys=[SCENE_0061],
        label_set_id="nuscenes-scene-0061",
    )
    assert (len(labels.coverage), len(labels.labels)) == (39, 4699)
    assert {a.source_id for a in labels.coverage} == {pinned.content_id}
    # Without unit_keys the slice names scenes whose files it does not hold.
    with pytest.raises(NuScenesLabelImportError, match="is not a file of"):
        _labels(NUSCENES, include=snapshots["nuscenes-scene-0061"]["include"])
