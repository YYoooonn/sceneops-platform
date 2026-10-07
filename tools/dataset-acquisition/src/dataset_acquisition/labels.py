"""Label documents: post-acquisition labels as a file next to the recording
(ADR-007 §33.2).

Ground truth is not an acquisition fact: a real robot never records it, and
the recording this tool writes carries no annotation. A source's annotations
are emitted separately, as a *label set document* the platform imports with
``IMPORT_LABELS``. The document anchors every label on an observation of the
recording by ``(robot_run_id, channel, source_clock, timestamp_ns)`` and
declares which anchors it annotated (its *coverage*), including anchors with
no object, so "annotated as empty" differs from "never annotated".

Two steps, split at the source boundary:

* **Extraction** (needs the source dataset; reference preparation only) writes
  a *reference label artifact*: the unit's annotations and key-frame anchors,
  with no RobotRun, baseline or any other runtime identity.
* **Rendering** (needs no source) turns a verified artifact and a target
  RobotRun into the document. The artifact is the only input, so a document
  is a deterministic function of (artifact, run id, label set id).

The document shape is the platform's ``sceneops.label_set/v1`` contract,
written as plain JSON: this tool imports no SceneOps package (I-36). The
platform validates and canonicalizes the document on import.
"""

from __future__ import annotations

import json
from typing import Any

from .events import AcquisitionError
from .nuscenes import (
    MAP_FRAME,
    SOURCE_FORMAT,
    KeyframeAnnotations,
    NuScenesAdapter,
    SourceAnnotation,
    lidar_topic,
)

LABEL_SET_SCHEMA = "sceneops.label_set/v1"
REFERENCE_LABELS_SCHEMA = "sceneops.reference_labels/1"
# The clock the recording's Header.stamp times are on (the build
# configuration declares the same clock for the channel).
HEADER_STAMP_CLOCK = "sensor.header_stamp"
# nuScenes annotates a sample at its lidar key frame.
ANCHOR_CHANNEL = "LIDAR_TOP"


def reference_labels(adapter: NuScenesAdapter) -> dict[str, Any]:
    """The reference label artifact of the adapter's unit.

    One entry per nuScenes sample, in sample order, anchored on that sample's
    ``ANCHOR_CHANNEL`` key frame observation (a sample without objects is kept:
    it is coverage). Boxes are in the global frame, which the recording
    publishes as ``box_frame``. Nothing here names a RobotRun, a baseline, a
    Scene or an Episode."""
    entries = adapter.keyframe_annotations(ANCHOR_CHANNEL)
    return {
        "schema": REFERENCE_LABELS_SCHEMA,
        "producer": SOURCE_FORMAT,
        "producer_version": adapter.selection.version,
        "source_unit": adapter.selection.source_unit,
        "anchor_topic": lidar_topic(ANCHOR_CHANNEL),
        "box_frame": MAP_FRAME,
        "samples": [
            {
                "sample_token": entry.sample_token,
                "anchor_timestamp_ns": entry.anchor_timestamp_ns,
                "labels": [
                    {
                        "label_id": ann.token,
                        "instance_id": ann.instance_token,
                        "category": ann.category,
                        "attributes": list(ann.attributes),
                        "center_m": list(ann.translation),
                        "size_wlh_m": list(ann.size_wlh),
                        "rotation_wxyz": list(ann.rotation_wxyz),
                    }
                    for ann in entry.annotations
                ],
            }
            for entry in entries
        ],
    }


def serialize_reference_labels(artifact: dict[str, Any]) -> bytes:
    """Canonical bytes of an artifact (sorted keys, no whitespace)."""
    return json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()


_ARTIFACT_KEYS = {
    "schema",
    "producer",
    "producer_version",
    "source_unit",
    "anchor_topic",
    "box_frame",
    "samples",
}
_SAMPLE_KEYS = {"sample_token", "anchor_timestamp_ns", "labels"}
_LABEL_KEYS = {
    "label_id",
    "instance_id",
    "category",
    "attributes",
    "center_m",
    "size_wlh_m",
    "rotation_wxyz",
}


def _fail(where: str, why: str) -> AcquisitionError:
    return AcquisitionError(f"reference labels: {where}: {why}")


def _keys(value: Any, where: str, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise _fail(where, f"expected an object with keys {sorted(expected)}")
    return value


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _fail(where, "expected a list of strings")
    return value


def _numbers(value: Any, where: str, length: int) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != length
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
    ):
        raise _fail(where, f"expected {length} numbers")
    return value


def parse_reference_labels(data: bytes) -> dict[str, Any]:
    """The artifact in ``data``, validated: every field a renderer reads is
    present and of the expected type."""
    try:
        artifact = json.loads(data)
    except ValueError as exc:
        raise _fail("document", f"not valid JSON: {exc}") from exc
    _keys(artifact, "document", _ARTIFACT_KEYS)
    if artifact["schema"] != REFERENCE_LABELS_SCHEMA:
        raise _fail("schema", f"must be {REFERENCE_LABELS_SCHEMA!r}")
    for key in (
        "producer",
        "producer_version",
        "source_unit",
        "anchor_topic",
        "box_frame",
    ):
        if not isinstance(artifact[key], str) or not artifact[key]:
            raise _fail(key, "expected a non-empty string")
    if not isinstance(artifact["samples"], list):
        raise _fail("samples", "expected a list")
    for index, sample in enumerate(artifact["samples"]):
        where = f"samples[{index}]"
        _keys(sample, where, _SAMPLE_KEYS)
        if not isinstance(sample["sample_token"], str):
            raise _fail(f"{where}.sample_token", "expected a string")
        stamp = sample["anchor_timestamp_ns"]
        if not isinstance(stamp, int) or isinstance(stamp, bool):
            raise _fail(f"{where}.anchor_timestamp_ns", "expected an integer")
        if not isinstance(sample["labels"], list):
            raise _fail(f"{where}.labels", "expected a list")
        for label_index, label in enumerate(sample["labels"]):
            at = f"{where}.labels[{label_index}]"
            _keys(label, at, _LABEL_KEYS)
            for key in ("label_id", "instance_id", "category"):
                if not isinstance(label[key], str) or not label[key]:
                    raise _fail(f"{at}.{key}", "expected a non-empty string")
            _strings(label["attributes"], f"{at}.attributes")
            _numbers(label["center_m"], f"{at}.center_m", 3)
            _numbers(label["size_wlh_m"], f"{at}.size_wlh_m", 3)
            _numbers(label["rotation_wxyz"], f"{at}.rotation_wxyz", 4)
    return artifact


def label_counts(artifact: dict[str, Any]) -> dict[str, int]:
    return {
        "sample_count": len(artifact["samples"]),
        "label_count": sum(len(s["labels"]) for s in artifact["samples"]),
    }


def render_label_document(
    artifact: dict[str, Any],
    *,
    robot_run_id: str,
    label_set_id: str,
    source_clock: str = HEADER_STAMP_CLOCK,
) -> dict[str, Any]:
    """The ``sceneops.label_set/v1`` document of ``artifact`` for one RobotRun.
    Needs no source dataset; ``artifact`` must come from
    :func:`parse_reference_labels`."""
    entries = [
        KeyframeAnnotations(
            sample_token=sample["sample_token"],
            anchor_channel=ANCHOR_CHANNEL,
            anchor_timestamp_ns=sample["anchor_timestamp_ns"],
            annotations=[
                SourceAnnotation(
                    token=label["label_id"],
                    instance_token=label["instance_id"],
                    category=label["category"],
                    translation=label["center_m"],
                    size_wlh=label["size_wlh_m"],
                    rotation_wxyz=label["rotation_wxyz"],
                    attributes=label["attributes"],
                )
                for label in sample["labels"]
            ],
        )
        for sample in artifact["samples"]
    ]
    return build_document(
        entries,
        topic=artifact["anchor_topic"],
        robot_run_id=robot_run_id,
        label_set_id=label_set_id,
        box_frame=artifact["box_frame"],
        source_clock=source_clock,
        producer_version=artifact["producer_version"],
    )


def build_document(
    entries: list[KeyframeAnnotations],
    *,
    topic: str,
    robot_run_id: str,
    label_set_id: str,
    box_frame: str,
    source_clock: str,
    producer_version: str,
) -> dict[str, Any]:
    coverage: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for entry in entries:
        anchor = {
            "robot_run_id": robot_run_id,
            "channel": topic,
            "source_clock": source_clock,
            "timestamp_ns": entry.anchor_timestamp_ns,
        }
        coverage.append(anchor)
        for ann in entry.annotations:
            labels.append(
                {
                    "label_id": ann.token,
                    "anchor": anchor,
                    "category": ann.category,
                    "instance_id": ann.instance_token,
                    "box": {
                        "frame_id": box_frame,
                        "center_m": ann.translation,
                        "size_wlh_m": ann.size_wlh,
                        "rotation_wxyz": ann.rotation_wxyz,
                    },
                    "attributes": ann.attributes,
                }
            )
    return {
        "schema_version": LABEL_SET_SCHEMA,
        "label_set_id": label_set_id,
        "provenance": {
            "kind": "external",
            "producer": SOURCE_FORMAT,
            "producer_version": producer_version,
        },
        "coverage": coverage,
        "labels": labels,
    }
