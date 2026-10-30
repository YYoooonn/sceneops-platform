"""The one label-anchor resolver on a Scene manifest (ADR-015 §9, M3.0 OQ-6)."""

from __future__ import annotations

import json

import pytest

from sceneops_core.labels import (
    AmbiguousAnchorError,
    GroupRefMismatchError,
    ObservationAnchor,
    ResolvedAnchor,
    SceneAnchorResolver,
    UnsupportedAnchorClockError,
    resolve_anchor,
)
from sceneops_core.provenance import ContentUnitSource
from sceneops_core.scenes.schemas import SceneManifest
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_core.sources import content_id_for

CLOCK = "mcap_log_time"
CHECKSUM = "sha256:" + "e" * 64
CONTENT = content_id_for(CHECKSUM)
LIDAR_1, LIDAR_2 = 1_000_000_000, 1_500_000_000


def scene(*, origin: str | None = None) -> SceneManifest:
    data = json.loads(
        build_scene_manifest(
            source=ContentUnitSource(
                content_id=CONTENT,
                content_checksum=CHECKSUM,
                source_clock=CLOCK,
                start_timestamp_ns=0,
                end_timestamp_ns=10_000_000_000,
                unit_key="scene-token",
            )
        ).to_canonical_bytes()
    )
    for group, ref in zip(data["groups"], ("sample-a", "sample-b"), strict=True):
        group["native_ref"] = ref
    if origin is not None:
        for domain in data["clock_domains"]:
            domain["origin"] = origin
    return SceneManifest.model_validate(data)


def anchor(
    ts: int = LIDAR_1,
    group_ref: str | None = "sample-a",
    *,
    channel: str = "LIDAR_TOP",
    source_kind: str = "content",
    source_id: str = CONTENT,
    clock: str = CLOCK,
) -> ObservationAnchor:
    return ObservationAnchor(
        source_kind=source_kind,
        source_id=source_id,
        channel=channel,
        source_clock=clock,
        timestamp_ns=ts,
        group_ref=group_ref,
    )


def test_a_group_anchor_resolves_to_its_observation_inside_its_group() -> None:
    m = scene()
    assert resolve_anchor(m, anchor()) == ResolvedAnchor(
        f"lidar-{LIDAR_1}", "keyframe-0000"
    )
    assert resolve_anchor(m, anchor(LIDAR_2, "sample-b")) == ResolvedAnchor(
        f"lidar-{LIDAR_2}", "keyframe-0001"
    )


def test_an_anchor_without_group_ref_resolves_to_its_observation_only() -> None:
    assert resolve_anchor(scene(), anchor(group_ref=None)) == ResolvedAnchor(
        f"lidar-{LIDAR_1}"
    )


@pytest.mark.parametrize(
    "other",
    [
        anchor(source_kind="recording"),
        anchor(source_id=content_id_for("sha256:" + "f" * 64)),
        anchor(channel="RADAR_FRONT"),
        # Another unit of the same content: neither its observation nor its group
        # is in this Scene.
        anchor(9_000_000_000, "sample-z"),
        anchor(9_000_000_000, None),
    ],
)
def test_an_anchor_of_another_scene_does_not_resolve(other) -> None:
    assert resolve_anchor(scene(), other) is None


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (anchor(LIDAR_1, "sample-b"), "not a member of group keyframe-0001"),
        (anchor(LIDAR_1, "sample-z"), "no group of the Scene"),
        (anchor(LIDAR_1 + 1, "sample-a"), "no 'LIDAR_TOP' observation at the anchor"),
        (anchor(clock="other_clock"), "no 'LIDAR_TOP' observation at the anchor"),
        # The camera key frame is in the group, but the anchor is on the lidar.
        (anchor(LIDAR_1 + 7), "no 'LIDAR_TOP' observation at the anchor"),
    ],
)
def test_an_inconsistent_group_ref_is_an_error_not_an_unattached_label(
    bad, message
) -> None:
    with pytest.raises(GroupRefMismatchError, match=message):
        resolve_anchor(scene(), bad)


def test_an_ambiguous_anchor_fails() -> None:
    data = json.loads(scene().to_canonical_bytes())
    twin = dict(next(o for o in data["observations"] if o["channel"] == "LIDAR_TOP"))
    twin["observation_id"] += "-twin"
    data["observations"].append(twin)
    data["observations"].sort(
        key=lambda o: (o["channel"], o["timestamp_ns"], o["observation_id"])
    )
    with pytest.raises(AmbiguousAnchorError):
        resolve_anchor(SceneManifest.model_validate(data), anchor(group_ref=None))


def test_a_unit_origin_clock_is_refused() -> None:
    with pytest.raises(UnsupportedAnchorClockError):
        resolve_anchor(scene(origin="unit"), anchor())


def test_one_resolver_answers_every_anchor_of_a_scene_alike() -> None:
    m = scene()
    resolver = SceneAnchorResolver(m)
    anchors = [anchor(), anchor(LIDAR_2, "sample-b"), anchor(group_ref=None)]
    assert [resolver.resolve(a) for a in anchors] == [
        resolve_anchor(m, a) for a in anchors
    ]
