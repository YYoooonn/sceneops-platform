"""Label documents: a source's annotations leave as a separate file,
anchored on observations of the recording, never inside it (ADR-007 §33.2)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from synthetic_nuscenes import ANNOTATIONS, UNIT, VERSION, write_dataroot

from dataset_acquisition.cli import main
from dataset_acquisition.events import AcquisitionError
from dataset_acquisition.labels import HEADER_STAMP_CLOCK, label_document
from dataset_acquisition.mcap_sink import write_mcap
from dataset_acquisition.nuscenes import NuScenesAdapter, NuScenesSelection


@pytest.fixture()
def adapter(tmp_path: Path) -> NuScenesAdapter:
    root = write_dataroot(tmp_path / "nuscenes", annotations=True)
    return NuScenesAdapter(
        NuScenesSelection(
            dataroot=root, version=VERSION, source_unit=UNIT, channel_groups=frozenset({"lidar"})
        )
    )


def _document(adapter: NuScenesAdapter) -> dict:
    return label_document(adapter, robot_run_id="run-1", label_set_id="gt-1")


def test_coverage_is_one_anchor_per_sample_on_the_lidar_key_frame(adapter):
    doc = _document(adapter)
    assert doc["schema_version"] == "sceneops.label_set/v1"
    assert [(a["channel"], a["timestamp_ns"]) for a in doc["coverage"]] == [
        ("/lidar/top/points", 1_000_050_000),
        ("/lidar/top/points", 1_500_050_000),
    ]
    assert {a["robot_run_id"] for a in doc["coverage"]} == {"run-1"}
    assert {a["source_clock"] for a in doc["coverage"]} == {HEADER_STAMP_CLOCK}


def test_labels_carry_boxes_categories_instances_and_attributes(adapter):
    doc = _document(adapter)
    assert [lab["label_id"] for lab in doc["labels"]] == ["ann-1", "ann-2", "ann-3"]
    first = doc["labels"][0]
    assert first["category"] == "vehicle.car"
    assert first["instance_id"] == "inst-1"
    assert first["attributes"] == ["vehicle.moving", "vehicle.stopped"]
    assert first["box"] == {
        "frame_id": "map",
        "center_m": ANNOTATIONS[0][3],
        "size_wlh_m": ANNOTATIONS[0][4],
        "rotation_wxyz": ANNOTATIONS[0][5],
    }
    assert doc["labels"][1]["category"] == "human.pedestrian.adult"
    # Each label anchors on its sample's lidar key frame.
    by_label = {lab["label_id"]: lab["anchor"]["timestamp_ns"] for lab in doc["labels"]}
    assert by_label == {"ann-1": 1_000_050_000, "ann-2": 1_000_050_000, "ann-3": 1_500_050_000}


def test_provenance_names_the_source_not_a_trust_level(adapter):
    assert _document(adapter)["provenance"] == {
        "kind": "external",
        "producer": "nuscenes",
        "producer_version": VERSION,
    }


def test_the_document_is_deterministic(adapter):
    assert json.dumps(_document(adapter), sort_keys=True) == json.dumps(
        _document(adapter), sort_keys=True
    )


def test_annotations_never_enter_the_recording(adapter, tmp_path):
    out = tmp_path / "rec.mcap"
    full = NuScenesAdapter(
        NuScenesSelection(
            dataroot=adapter.selection.dataroot, version=VERSION, source_unit=UNIT
        )
    )
    write_mcap(full.events(), out, origin=full.origin())
    assert b"ann-1" not in out.read_bytes()
    assert b"vehicle.car" not in out.read_bytes()


def test_a_missing_anchor_channel_fails_loudly(adapter):
    with pytest.raises(AcquisitionError, match="no 'LIDAR_FRONT'"):
        label_document(
            adapter, robot_run_id="run-1", label_set_id="gt", anchor_channel="LIDAR_FRONT"
        )


def test_the_command_writes_the_document(tmp_path, capsys):
    root = write_dataroot(tmp_path / "nuscenes", annotations=True)
    out = tmp_path / "out" / "labels.json"
    code = main(
        [
            "nuscenes-labels",
            "--dataroot", str(root),
            "--version", VERSION,
            "--source-unit", UNIT,
            "--robot-run-id", "run-9",
            "--label-set-id", "gt-9",
            "--output", str(out),
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert (summary["coverage_count"], summary["label_count"]) == (2, 3)
    doc = json.loads(out.read_text())
    assert doc["label_set_id"] == "gt-9"
    assert {lab["anchor"]["robot_run_id"] for lab in doc["labels"]} == {"run-9"}


def test_the_command_reports_unknown_units(tmp_path, capsys):
    root = write_dataroot(tmp_path / "nuscenes", annotations=True)
    code = main(
        [
            "nuscenes-labels",
            "--dataroot", str(root),
            "--version", VERSION,
            "--source-unit", "scene-9999",
            "--robot-run-id", "run-1",
            "--label-set-id", "gt",
            "--output", str(tmp_path / "x.json"),
        ]
    )
    assert code == 1
    assert "label export failed" in capsys.readouterr().err


REAL = Path(
    os.environ.get(
        "NUSCENES_DATAROOT", Path(__file__).resolve().parents[3] / "data/raw/nuscenes"
    )
)


@pytest.mark.skipif(
    not (REAL / "v1.0-mini" / "scene.json").is_file(), reason="nuScenes v1.0-mini absent"
)
def test_real_mini_scene_labels_match_the_source_tables():
    scene = "scene-0061"
    adapter = NuScenesAdapter(
        NuScenesSelection(
            dataroot=REAL, version="v1.0-mini", source_unit=scene, channel_groups=frozenset({"lidar"})
        )
    )
    doc = label_document(adapter, robot_run_id="run", label_set_id="gt")
    nusc = adapter._nusc
    token = adapter._scene["first_sample_token"]
    samples = []
    while token:
        samples.append(nusc.get("sample", token))
        token = samples[-1]["next"]
    assert len(doc["coverage"]) == len(samples) == adapter._scene["nbr_samples"]
    assert len(doc["labels"]) == sum(len(s["anns"]) for s in samples)
    assert len({lab["label_id"] for lab in doc["labels"]}) == len(doc["labels"])
    assert all(lab["box"]["size_wlh_m"][0] > 0 for lab in doc["labels"])
