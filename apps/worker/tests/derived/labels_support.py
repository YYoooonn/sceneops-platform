"""Label set documents for derived-workflow tests."""

from __future__ import annotations

from sceneops_core.labels import (
    Box3DLabel,
    LabelBox3D,
    LabelProvenance,
    LabelSetManifest,
    LabelSourceKind,
    ObservationAnchor,
)

CLOCK = "mcap_log_time"


def anchor(run: str, ts: int, channel: str = "LIDAR_TOP") -> ObservationAnchor:
    return ObservationAnchor(
        robot_run_id=run, channel=channel, source_clock=CLOCK, timestamp_ns=ts
    )


def box_label(
    label_id: str,
    run: str,
    ts: int,
    *,
    x: float = 10.0,
    category: str = "vehicle.car",
    frame: str = "world",
) -> Box3DLabel:
    return Box3DLabel(
        label_id=label_id,
        anchor=anchor(run, ts),
        category=category,
        box=LabelBox3D(
            frame_id=frame,
            center_m=(x, 2.0, 0.5),
            size_wlh_m=(1.9, 4.5, 1.6),
            rotation_wxyz=(1.0, 0.0, 0.0, 0.0),
        ),
    )


def label_document(
    label_set_id: str,
    *,
    run: str = "run-001",
    covered: list[int],
    labels: dict[str, tuple[int, float]] | None = None,
    kind: LabelSourceKind = LabelSourceKind.EXTERNAL,
) -> LabelSetManifest:
    """``labels`` maps label id -> (anchor timestamp, x)."""
    return LabelSetManifest.normalized(
        label_set_id=label_set_id,
        provenance=LabelProvenance(
            kind=kind, producer="nuscenes", producer_version="v1.0-mini"
        ),
        coverage=[anchor(run, ts) for ts in covered],
        labels=[
            box_label(lid, run, ts, x=x) for lid, (ts, x) in (labels or {}).items()
        ],
    )


async def write_document(
    harness, name: str, manifest: LabelSetManifest, *, canonical: bool = True
) -> str:
    uri = harness.artifact_store.join_uri(harness.root, "incoming", name)
    data = (
        manifest.to_canonical_bytes()
        if canonical
        else manifest.model_dump_json(indent=2).encode()
    )
    await harness.artifact_store.write_bytes(uri, data)
    return uri
