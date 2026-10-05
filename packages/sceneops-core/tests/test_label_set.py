"""LabelSetManifest v1: canonical form, provenance and coverage invariants
(ADR-007 §33.2)."""

from __future__ import annotations

import json

import pytest

from sceneops_core.common.derived_ids import label_set_artifact_id
from sceneops_core.labels import (
    Box3DLabel,
    LabelBox3D,
    LabelProvenance,
    LabelSetError,
    LabelSetManifest,
    LabelSourceKind,
    NonCanonicalLabelSetError,
    ObservationAnchor,
    UnsupportedLabelSetVersionError,
    load_canonical_label_set,
    parse_label_set_document,
)

RUN = "run-001"


def anchor(ts: int, *, channel: str = "LIDAR_TOP", run: str = RUN) -> ObservationAnchor:
    return ObservationAnchor(
        robot_run_id=run, channel=channel, source_clock="mcap_log_time", timestamp_ns=ts
    )


def box(label_id: str, ts: int, *, x: float = 1.0) -> Box3DLabel:
    return Box3DLabel(
        label_id=label_id,
        anchor=anchor(ts),
        category="vehicle.car",
        box=LabelBox3D(
            frame_id="world",
            center_m=(x, 0.0, 0.0),
            size_wlh_m=(1.0, 2.0, 1.5),
            rotation_wxyz=(1.0, 0.0, 0.0, 0.0),
        ),
    )


HUMAN = LabelProvenance(kind=LabelSourceKind.HUMAN, producer="annotation-team")


def make(labels: list[Box3DLabel], coverage: list[int]) -> LabelSetManifest:
    return LabelSetManifest.normalized(
        label_set_id="gt-1",
        provenance=HUMAN,
        coverage=[anchor(ts) for ts in coverage],
        labels=labels,
    )


def test_normalized_orders_and_deduplicates_coverage() -> None:
    manifest = make([box("b", 20), box("a", 10)], coverage=[20, 10, 10])
    assert [a.timestamp_ns for a in manifest.coverage] == [10, 20]
    assert [label.label_id for label in manifest.labels] == ["a", "b"]


def test_canonical_bytes_round_trip_and_checksum_is_stable() -> None:
    manifest = make([box("a", 10)], coverage=[10, 20])
    data = manifest.to_canonical_bytes()
    assert load_canonical_label_set(data) == manifest
    assert manifest.checksum() == make([box("a", 10)], coverage=[20, 10]).checksum()


def test_input_order_never_changes_the_revision() -> None:
    first = make([box("a", 10), box("b", 20)], coverage=[10, 20])
    second = make([box("b", 20), box("a", 10)], coverage=[20, 10])
    assert first.to_canonical_bytes() == second.to_canonical_bytes()


def test_noncanonical_bytes_are_rejected() -> None:
    manifest = make([box("a", 10)], coverage=[10])
    pretty = json.dumps(manifest.model_dump(mode="json"), indent=2).encode()
    with pytest.raises(NonCanonicalLabelSetError):
        load_canonical_label_set(pretty)


def test_unknown_schema_version_is_rejected() -> None:
    payload = make([box("a", 10)], coverage=[10]).model_dump(mode="json")
    payload["schema_version"] = "sceneops.label_set/v9"
    with pytest.raises(UnsupportedLabelSetVersionError):
        load_canonical_label_set(json.dumps(payload).encode())


def test_label_outside_declared_coverage_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside the declared coverage"):
        make([box("a", 10)], coverage=[20])


def test_duplicate_label_ids_are_rejected_not_merged() -> None:
    with pytest.raises(ValueError, match="unique"):
        make([box("a", 10), box("a", 20)], coverage=[10, 20])


def test_coverage_may_include_anchors_without_labels() -> None:
    manifest = make([], coverage=[10, 20])
    assert manifest.labels == []
    assert len(manifest.coverage) == 2


def test_empty_coverage_is_rejected() -> None:
    with pytest.raises(ValueError):
        make([], coverage=[])


def test_model_labels_must_name_their_model_revision() -> None:
    with pytest.raises(ValueError, match="model_id and model_version"):
        LabelProvenance(kind=LabelSourceKind.MODEL, producer="detector")
    ok = LabelProvenance(
        kind=LabelSourceKind.MODEL,
        producer="detector",
        model_id="grounding-dino",
        model_version="tiny",
    )
    assert ok.model_version == "tiny"


def test_only_model_labels_may_name_a_model() -> None:
    with pytest.raises(ValueError, match="only model-generated"):
        LabelProvenance(
            kind=LabelSourceKind.HUMAN, producer="team", model_id="m", model_version="1"
        )


def test_producer_must_be_a_canonical_identifier() -> None:
    with pytest.raises(ValueError):
        LabelProvenance(kind=LabelSourceKind.EXTERNAL, producer="NuScenes")


def test_box_size_must_be_non_negative_and_rotation_a_unit_quaternion() -> None:
    with pytest.raises(ValueError):
        LabelBox3D(
            frame_id="world",
            center_m=(0, 0, 0),
            size_wlh_m=(-1, 1, 1),
            rotation_wxyz=(1, 0, 0, 0),
        )
    with pytest.raises(ValueError):
        LabelBox3D(
            frame_id="world",
            center_m=(0, 0, 0),
            size_wlh_m=(1, 1, 1),
            rotation_wxyz=(2, 0, 0, 0),
        )


def test_attributes_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        Box3DLabel(
            label_id="a",
            anchor=anchor(10),
            category="c",
            box=box("a", 10).box,
            attributes=["b", "a"],
        )


def test_document_parser_normalizes_unordered_adapter_output() -> None:
    manifest = make([box("a", 10), box("b", 20)], coverage=[10, 20])
    document = manifest.model_dump(mode="json")
    document["coverage"].reverse()
    document["labels"].reverse()
    parsed = parse_label_set_document(json.dumps(document, indent=2).encode())
    assert parsed.to_canonical_bytes() == manifest.to_canonical_bytes()


def test_document_parser_reports_invalid_documents_as_label_set_errors() -> None:
    with pytest.raises(LabelSetError):
        parse_label_set_document(b"not json")
    with pytest.raises(LabelSetError):
        parse_label_set_document(b'{"label_set_id": "x"}')


def test_artifact_id_is_deterministic_per_revision() -> None:
    a = label_set_artifact_id(label_set_id="gt-1", checksum="sha256:" + "a" * 64)
    assert a == label_set_artifact_id(
        label_set_id="gt-1", checksum="sha256:" + "a" * 64
    )
    assert a != label_set_artifact_id(
        label_set_id="gt-1", checksum="sha256:" + "b" * 64
    )
    assert a != label_set_artifact_id(
        label_set_id="gt-2", checksum="sha256:" + "a" * 64
    )
