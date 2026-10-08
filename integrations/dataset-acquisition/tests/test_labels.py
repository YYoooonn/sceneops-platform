"""Label documents: a source's annotations leave as a separate file,
anchored on observations of the recording, never inside it (ADR-007 §33.2).

Extraction reads the source once into a baseline-neutral reference label
artifact; a document is rendered from that artifact alone."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from synthetic_nuscenes import ANNOTATIONS, UNIT, VERSION, write_dataroot

from dataset_acquisition.events import AcquisitionError
from dataset_acquisition.labels import (
    HEADER_STAMP_CLOCK,
    build_document,
    label_counts,
    parse_reference_labels,
    reference_labels,
    render_label_document,
    serialize_reference_labels,
)
from dataset_acquisition.mcap_sink import write_mcap
from dataset_acquisition.nuscenes import NuScenesAdapter, NuScenesSelection


@pytest.fixture()
def adapter(tmp_path: Path) -> NuScenesAdapter:
    root = write_dataroot(tmp_path / "nuscenes", annotations=True)
    return NuScenesAdapter(
        NuScenesSelection(
            dataroot=root,
            version=VERSION,
            source_unit=UNIT,
            channel_groups=frozenset({"lidar"}),
        )
    )


def _artifact(adapter: NuScenesAdapter) -> dict:
    return parse_reference_labels(serialize_reference_labels(reference_labels(adapter)))


def _document(
    adapter: NuScenesAdapter, run: str = "run-1", label_set: str = "gt-1"
) -> dict:
    return render_label_document(
        _artifact(adapter), robot_run_id=run, label_set_id=label_set
    )


def test_the_artifact_names_no_runtime_identity(adapter):
    artifact = _artifact(adapter)
    text = json.dumps(artifact)
    assert artifact["schema"] == "sceneops.reference_labels/1"
    assert artifact["source_unit"] == UNIT
    assert "robot_run" not in text and "label_set" not in text and "run-" not in text
    assert [s["anchor_timestamp_ns"] for s in artifact["samples"]] == [
        1_000_050_000,
        1_500_050_000,
    ]
    assert label_counts(artifact) == {"sample_count": 2, "label_count": 3}


def test_an_unannotated_sample_stays_in_the_artifact_as_coverage(tmp_path):
    root = write_dataroot(tmp_path / "nuscenes")  # no annotation tables
    bare = NuScenesAdapter(
        NuScenesSelection(dataroot=root, version=VERSION, source_unit=UNIT)
    )
    artifact = parse_reference_labels(
        serialize_reference_labels(reference_labels(bare))
    )
    assert label_counts(artifact) == {"sample_count": 2, "label_count": 0}
    doc = render_label_document(artifact, robot_run_id="run-1", label_set_id="gt")
    assert len(doc["coverage"]) == 2 and doc["labels"] == []


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
    assert by_label == {
        "ann-1": 1_000_050_000,
        "ann-2": 1_000_050_000,
        "ann-3": 1_500_050_000,
    }


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
    assert serialize_reference_labels(
        reference_labels(adapter)
    ) == serialize_reference_labels(reference_labels(adapter))


def test_run_identity_is_injected_only_at_render_time(adapter):
    first, second = (
        _document(adapter, "run-a", "gt-a"),
        _document(adapter, "run-b", "gt-b"),
    )
    assert first["label_set_id"] == "gt-a" and second["label_set_id"] == "gt-b"
    assert {lab["anchor"]["robot_run_id"] for lab in first["labels"]} == {"run-a"}
    assert {a["robot_run_id"] for a in second["coverage"]} == {"run-b"}
    # Apart from the injected identity the two documents are the same.
    first_text = (
        json.dumps(first, sort_keys=True).replace("run-a", "R").replace("gt-a", "G")
    )
    second_text = (
        json.dumps(second, sort_keys=True).replace("run-b", "R").replace("gt-b", "G")
    )
    assert first_text == second_text


def test_the_rendered_document_equals_the_one_built_straight_from_the_source(adapter):
    entries = adapter.keyframe_annotations("LIDAR_TOP")
    direct = build_document(
        entries,
        topic="/lidar/top/points",
        robot_run_id="run-1",
        label_set_id="gt-1",
        box_frame="map",
        source_clock=HEADER_STAMP_CLOCK,
        producer_version=VERSION,
    )
    assert json.dumps(_document(adapter), sort_keys=True) == json.dumps(
        direct, sort_keys=True
    )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda a: a.update(schema="other/1"), "schema"),
        (lambda a: a.update(extra=1), "expected an object"),
        (lambda a: a.pop("box_frame"), "expected an object"),
        (
            lambda a: a["samples"][0].update(anchor_timestamp_ns=1.5),
            "anchor_timestamp_ns",
        ),
        (lambda a: a["samples"][0]["labels"][0].update(center_m=[1, 2]), "center_m"),
        (lambda a: a["samples"][0]["labels"][0].update(category=""), "category"),
        (lambda a: a["samples"][0]["labels"][0].update(attributes="x"), "attributes"),
    ],
)
def test_a_malformed_artifact_is_refused(adapter, mutate, message):
    artifact = reference_labels(adapter)
    mutate(artifact)
    with pytest.raises(AcquisitionError, match=message):
        parse_reference_labels(json.dumps(artifact).encode())


def test_a_non_json_artifact_is_refused():
    with pytest.raises(AcquisitionError, match="not valid JSON"):
        parse_reference_labels(b"{")


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


REAL = Path(
    os.environ.get(
        "NUSCENES_DATAROOT", Path(__file__).resolve().parents[3] / "data/raw/nuscenes"
    )
)


@pytest.mark.skipif(
    not (REAL / "v1.0-mini" / "scene.json").is_file(),
    reason="nuScenes v1.0-mini absent",
)
def test_real_mini_scene_labels_match_the_source_tables():
    scene = "scene-0061"
    adapter = NuScenesAdapter(
        NuScenesSelection(
            dataroot=REAL,
            version="v1.0-mini",
            source_unit=scene,
            channel_groups=frozenset({"lidar"}),
        )
    )
    doc = render_label_document(
        parse_reference_labels(serialize_reference_labels(reference_labels(adapter))),
        robot_run_id="run",
        label_set_id="gt",
    )
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
