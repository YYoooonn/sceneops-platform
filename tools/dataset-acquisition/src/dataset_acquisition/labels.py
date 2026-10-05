"""Label documents: post-acquisition labels as a file next to the recording
(ADR-007 §33.2).

Ground truth is not an acquisition fact: a real robot never records it, and
the recording this tool writes carries no annotation. A source's annotations
are emitted separately, as a *label set document* the platform imports with
``IMPORT_LABELS``. The document anchors every label on an observation of the
recording by ``(robot_run_id, channel, source_clock, timestamp_ns)`` and
declares which anchors it annotated (its *coverage*), including anchors with
no object, so "annotated as empty" differs from "never annotated".

The document shape is the platform's ``sceneops.label_set/v1`` contract,
written as plain JSON: this tool imports no SceneOps package (I-36). The
platform validates and canonicalizes the document on import.
"""

from __future__ import annotations

from typing import Any

from .nuscenes import (
    MAP_FRAME,
    SOURCE_FORMAT,
    KeyframeAnnotations,
    NuScenesAdapter,
    lidar_topic,
)

LABEL_SET_SCHEMA = "sceneops.label_set/v1"
# The clock the recording's Header.stamp times are on (the build
# configuration declares the same clock for the channel).
HEADER_STAMP_CLOCK = "sensor.header_stamp"


def label_document(
    adapter: NuScenesAdapter,
    *,
    robot_run_id: str,
    label_set_id: str,
    anchor_channel: str = "LIDAR_TOP",
    box_frame: str = MAP_FRAME,
    source_clock: str = HEADER_STAMP_CLOCK,
) -> dict[str, Any]:
    """The nuScenes annotations of the adapter's unit as a label set document.

    Anchors sit on the ``anchor_channel`` key frame observation of each
    sample (nuScenes annotates a sample at its lidar key frame). Boxes are in
    the global frame, which the recording publishes as ``box_frame``."""
    entries = adapter.keyframe_annotations(anchor_channel)
    topic = lidar_topic(anchor_channel)
    return build_document(
        entries,
        topic=topic,
        robot_run_id=robot_run_id,
        label_set_id=label_set_id,
        box_frame=box_frame,
        source_clock=source_clock,
        producer_version=adapter.selection.version,
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
