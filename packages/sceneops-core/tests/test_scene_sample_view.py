"""SceneSampleView v1: policy-driven association, label attachment and
canonical form (ADR-007 §33.3)."""

from __future__ import annotations

import json

import pytest

from sceneops_core.common.derived_ids import label_set_artifact_id
from sceneops_core.labels import (
    Box3DLabel,
    LabelBox3D,
    LabelProvenance,
    LabelSetManifest,
    LabelSetRef,
    LabelSourceKind,
    ObservationAnchor,
)
from sceneops_core.common.derived_ids import sample_view_artifact_id
from sceneops_core.sample_views import (
    AmbiguousAnchorError,
    AssociationMode,
    MemberPolicy,
    NonCanonicalSampleViewError,
    PosePolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
    SampleViewPolicyError,
    SampleViewResolutionError,
    SceneLacksAnchorChannelError,
    SceneRevisionRef,
    SceneSampleResolver,
    build_scene_sample_view,
    load_canonical_sample_view,
)
from sceneops_core.scenes.schemas import SceneManifest
from sceneops_core.scenes.testing import build_scene_manifest

CLOCK = "mcap_log_time"
RUN = "run-001"
MS = 1_000_000


def scene_ref(manifest: SceneManifest) -> SceneRevisionRef:
    return SceneRevisionRef(
        scene_id="scene-1",
        manifest_artifact_id="scene-manifest-1",
        manifest_checksum=manifest.checksum(),
    )


def manifest(**kwargs) -> SceneManifest:
    return build_scene_manifest(**kwargs)


def policy(
    *,
    lidar: MemberPolicy | None = None,
    pose: PosePolicy | None = None,
    stride: int = 1,
    anchor: str = "CAM_FRONT",
    members: list[MemberPolicy] | None = None,
) -> SampleViewPolicy:
    return SampleViewPolicy(
        anchor=SampleAnchorPolicy(channel=anchor, stride=stride),
        members=members
        if members is not None
        else [lidar or MemberPolicy(channel="LIDAR_TOP", tolerance_ns=10 * MS)],
        pose=pose,
    )


def build(m: SceneManifest, p: SampleViewPolicy, label_sets=()):
    return build_scene_sample_view(
        scene=scene_ref(m), manifest=m, policy=p, label_sets=label_sets
    )


def label_set(
    anchors: list[int], labels: dict[str, int], *, set_id: str = "gt-1", run: str = RUN
) -> tuple[LabelSetRef, LabelSetManifest]:
    def anchor(ts: int) -> ObservationAnchor:
        return ObservationAnchor(
            robot_run_id=run, channel="LIDAR_TOP", source_clock=CLOCK, timestamp_ns=ts
        )

    manifest = LabelSetManifest.normalized(
        label_set_id=set_id,
        provenance=LabelProvenance(kind=LabelSourceKind.HUMAN, producer="annotators"),
        coverage=[anchor(ts) for ts in anchors],
        labels=[
            Box3DLabel(
                label_id=label_id,
                anchor=anchor(ts),
                category="vehicle.car",
                box=LabelBox3D(
                    frame_id="world",
                    center_m=(1.0, 0.0, 0.0),
                    size_wlh_m=(1.0, 2.0, 1.5),
                    rotation_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            )
            for label_id, ts in labels.items()
        ],
    )
    checksum = manifest.checksum()
    return (
        LabelSetRef(
            label_set_id=set_id,
            manifest_artifact_id=label_set_artifact_id(
                label_set_id=set_id, checksum=checksum
            ),
            manifest_checksum=checksum,
        ),
        manifest,
    )


# Default Scene: camera at 1e9+7, sweep 1.25e9, 1.5e9+7; lidar at 1e9, 1.5e9.


def test_nearest_association_inside_tolerance_and_drop_outside() -> None:
    view = build(manifest(), policy())
    assert [s.anchor.observation_id for s in view.samples] == [
        "cam-1000000007",
        "cam-1500000007",
    ]
    first = view.samples[0]
    assert first.sample_id == "smp-000000"
    assert first.members[0].observation_id == "lidar-1000000000"
    assert first.members[0].time_delta_ns == -7
    # The sweep camera frame has no lidar within 10 ms: dropped, with the reason.
    assert [(d.observation_id, d.reasons) for d in view.dropped] == [
        ("cam-1250000000", ["missing_member:LIDAR_TOP"])
    ]


def test_optional_member_leaves_the_sample_without_it() -> None:
    p = policy(
        lidar=MemberPolicy(channel="LIDAR_TOP", tolerance_ns=10 * MS, required=False)
    )
    view = build(manifest(), p)
    assert len(view.samples) == 3
    sweep = view.samples[1]
    assert sweep.anchor.observation_id == "cam-1250000000"
    assert sweep.members == []
    assert view.dropped == []


def test_previous_association_never_looks_ahead() -> None:
    # Lidar at 1.0e9 is *before* camera at 1.0e9+7; lidar 1.5e9 is before 1.5e9+7.
    p = policy(
        lidar=MemberPolicy(
            channel="LIDAR_TOP",
            association=AssociationMode.PREVIOUS,
            tolerance_ns=10 * MS,
        )
    )
    view = build(manifest(), p)
    assert all(m.time_delta_ns <= 0 for s in view.samples for m in s.members)
    # Anchor *before* the only lidar: nothing previous within tolerance.
    p2 = policy(
        anchor="LIDAR_TOP",
        members=[
            MemberPolicy(
                channel="CAM_FRONT",
                association=AssociationMode.PREVIOUS,
                tolerance_ns=10 * MS,
            )
        ],
    )
    assert build(manifest(), p2).samples == []


def test_nearest_ties_go_to_the_earlier_timestamp() -> None:
    # Lidar at 1e9 and 1e9+28; the sweep camera lands exactly between them.
    m = manifest(keyframe_timestamps_ns=(1_000_000_000, 1_000_000_028))
    view = build(m, policy(lidar=MemberPolicy(channel="LIDAR_TOP", tolerance_ns=MS)))
    sweep = next(s for s in view.samples if s.anchor.timestamp_ns == 1_000_000_014)
    assert sweep.members[0].observation_id == "lidar-1000000000"
    assert sweep.members[0].time_delta_ns == -14


def test_stride_selects_every_nth_anchor_in_canonical_order() -> None:
    p = policy(
        stride=2,
        lidar=MemberPolicy(channel="LIDAR_TOP", tolerance_ns=10 * MS, required=False),
    )
    view = build(manifest(), p)
    assert [s.sample_id for s in view.samples] == ["smp-000000", "smp-000002"]


def test_pose_is_associated_on_the_anchor_clock() -> None:
    p = policy(
        pose=PosePolicy(parent_frame_id="world", child_frame_id="ego", tolerance_ns=MS)
    )
    view = build(manifest(), p)
    assert view.samples[0].pose is not None
    assert view.samples[0].pose.pose_id == "pose-cam-1000000007"
    assert view.samples[0].pose.time_delta_ns == 0


def test_required_pose_missing_drops_the_sample() -> None:
    p = policy(
        pose=PosePolicy(
            parent_frame_id="world", child_frame_id="nowhere", tolerance_ns=MS
        )
    )
    view = build(manifest(), p)
    assert view.samples == []
    assert all("missing_pose" in d.reasons for d in view.dropped)


def test_association_never_crosses_clocks() -> None:
    m = manifest(camera_clock="sensor.header_stamp")
    with pytest.raises(SampleViewPolicyError, match="anchor clock"):
        build(m, policy())


def test_only_poses_on_the_anchor_clock_are_used() -> None:
    m = manifest(camera_clock="sensor.header_stamp")
    p = SampleViewPolicy(
        anchor=SampleAnchorPolicy(channel="LIDAR_TOP"),
        pose=PosePolicy(parent_frame_id="world", child_frame_id="ego", tolerance_ns=MS),
    )
    view = build(m, p)
    assert view.samples and all(s.pose is not None for s in view.samples)
    assert all(s.pose.pose_id.startswith("pose-lidar-") for s in view.samples)


def test_a_pose_pair_only_on_another_clock_is_a_configuration_error() -> None:
    m = manifest(camera_clock="sensor.header_stamp")
    data = json.loads(m.to_canonical_bytes())
    data["poses"] = [
        p for p in data["poses"] if p["source_clock"] == "sensor.header_stamp"
    ]
    kept = {p["pose_id"] for p in data["poses"]}
    for observation in data["observations"]:
        if observation["ego_pose_id"] not in kept:
            observation["ego_pose_id"] = None
    only_camera_clock = SceneManifest.model_validate(data)
    p = SampleViewPolicy(
        anchor=SampleAnchorPolicy(channel="LIDAR_TOP"),
        pose=PosePolicy(parent_frame_id="world", child_frame_id="ego", tolerance_ns=MS),
    )
    with pytest.raises(SampleViewPolicyError, match="other than the anchor clock"):
        build(only_camera_clock, p)


def test_missing_anchor_channel_is_a_distinguishable_error() -> None:
    with pytest.raises(SceneLacksAnchorChannelError):
        build(manifest(), policy(anchor="CAM_BACK", members=[]))


def test_labels_attach_through_member_observations() -> None:
    ref, labels = label_set(
        anchors=[1_000_000_000, 1_500_000_000], labels={"a": 1_000_000_000}
    )
    view = build(manifest(), policy(), [(ref, labels)])
    first, second = view.samples
    assert first.labels[0].covered and first.labels[0].label_ids == ["a"]
    # Covered but empty is an annotated negative, distinct from uncovered.
    assert second.labels[0].covered and second.labels[0].label_ids == []
    stats = view.label_stats[0]
    assert (stats.covered_observation_count, stats.attached_label_count) == (2, 1)
    assert stats.unattached_label_count == 0


def test_uncovered_sample_is_not_a_negative() -> None:
    ref, labels = label_set(anchors=[1_000_000_000], labels={})
    view = build(manifest(), policy(), [(ref, labels)])
    assert view.samples[0].labels[0].covered
    assert not view.samples[1].labels[0].covered


def test_a_shared_observation_is_owned_by_the_nearest_sample() -> None:
    # Two camera anchors share the one lidar observation within tolerance.
    m = manifest(keyframe_timestamps_ns=(1_000_000_000, 1_020_000_000))
    p = policy(lidar=MemberPolicy(channel="LIDAR_TOP", tolerance_ns=100 * MS))
    ref, labels = label_set(anchors=[1_000_000_000], labels={"a": 1_000_000_000})
    view = build(m, p, [(ref, labels)])
    owners = [s.sample_id for s in view.samples if s.labels[0].label_ids == ["a"]]
    assert len(owners) == 1
    owner = next(s for s in view.samples if s.labels[0].label_ids)
    # camera 1e9+7 is nearer to lidar 1e9 than camera sweep 1.01e9.
    assert owner.anchor.timestamp_ns == 1_000_000_007
    assert view.label_stats[0].attached_label_count == 1


def test_labels_on_observations_no_sample_holds_are_counted_unattached() -> None:
    ref, labels = label_set(anchors=[1_500_000_000], labels={"a": 1_500_000_000})
    # stride 3 keeps only the first camera frame, so the second keyframe's
    # lidar observation is held by no sample.
    p = policy(stride=3, lidar=MemberPolicy(channel="LIDAR_TOP", tolerance_ns=10 * MS))
    view = build(manifest(), p, [(ref, labels)])
    assert [s.sample_id for s in view.samples] == ["smp-000000"]
    stats = view.label_stats[0]
    assert (stats.attached_label_count, stats.unattached_label_count) == (0, 1)


def test_labels_of_another_robot_run_are_ignored() -> None:
    ref, labels = label_set(
        anchors=[1_000_000_000], labels={"a": 1_000_000_000}, run="some-other-run"
    )
    view = build(manifest(), policy(), [(ref, labels)])
    assert all(not s.labels[0].covered for s in view.samples)
    assert view.label_stats[0].covered_observation_count == 0


def test_duplicate_anchor_resolution_fails_loudly() -> None:
    m = manifest()
    data = json.loads(m.to_canonical_bytes())
    twin = dict(next(o for o in data["observations"] if o["channel"] == "LIDAR_TOP"))
    twin["observation_id"] = twin["observation_id"] + "-twin"
    data["observations"].append(twin)
    data["observations"].sort(
        key=lambda o: (o["channel"], o["timestamp_ns"], o["observation_id"])
    )
    twinned = SceneManifest.model_validate(data)
    ref, labels = label_set(anchors=[1_000_000_000], labels={"a": 1_000_000_000})
    with pytest.raises(AmbiguousAnchorError):
        build(twinned, policy(), [(ref, labels)])


def test_view_is_deterministic_and_label_pin_order_does_not_matter() -> None:
    a = label_set(anchors=[1_000_000_000], labels={"a": 1_000_000_000}, set_id="gt-a")
    b = label_set(anchors=[1_500_000_000], labels={"b": 1_500_000_000}, set_id="gt-b")
    m = manifest()
    first = build(m, policy(), [a, b])
    second = build(m, policy(), [b, a])
    assert first.to_canonical_bytes() == second.to_canonical_bytes()
    assert [r.label_set_id for r in first.label_sets] == ["gt-a", "gt-b"]


def test_a_label_set_cannot_be_pinned_twice() -> None:
    a = label_set(anchors=[1_000_000_000], labels={})
    with pytest.raises(SampleViewPolicyError, match="only once"):
        build(manifest(), policy(), [a, a])


def test_policy_identity_ignores_member_order_and_rejects_anchor_member() -> None:
    m1 = MemberPolicy(channel="A", tolerance_ns=1)
    m2 = MemberPolicy(channel="B", tolerance_ns=1)
    one = SampleViewPolicy(anchor=SampleAnchorPolicy(channel="C"), members=[m1, m2])
    two = SampleViewPolicy(anchor=SampleAnchorPolicy(channel="C"), members=[m2, m1])
    assert one.checksum() == two.checksum()
    with pytest.raises(ValueError, match="anchor channel"):
        SampleViewPolicy(anchor=SampleAnchorPolicy(channel="A"), members=[m1])
    with pytest.raises(ValueError, match="at most once"):
        SampleViewPolicy(anchor=SampleAnchorPolicy(channel="C"), members=[m1, m1])


def test_every_policy_field_changes_the_revision() -> None:
    m = manifest()
    base = build(m, policy()).checksum()
    assert build(m, policy(stride=2)).checksum() != base
    assert (
        build(
            m, policy(lidar=MemberPolicy(channel="LIDAR_TOP", tolerance_ns=11 * MS))
        ).checksum()
        != base
    )


def test_canonical_round_trip_and_noncanonical_rejection() -> None:
    view = build(manifest(), policy())
    data = view.to_canonical_bytes()
    assert load_canonical_sample_view(data) == view
    with pytest.raises(NonCanonicalSampleViewError):
        load_canonical_sample_view(
            json.dumps(view.model_dump(mode="json"), indent=1).encode()
        )


def test_artifact_id_is_scene_and_revision_scoped() -> None:
    a = sample_view_artifact_id(scene_id="scene-1", checksum="sha256:" + "a" * 64)
    assert a == sample_view_artifact_id(
        scene_id="scene-1", checksum="sha256:" + "a" * 64
    )
    assert a != sample_view_artifact_id(
        scene_id="scene-2", checksum="sha256:" + "a" * 64
    )
    assert a != sample_view_artifact_id(
        scene_id="scene-1", checksum="sha256:" + "b" * 64
    )


def test_resolver_joins_the_view_with_its_pinned_scene() -> None:
    m = manifest()
    view = build(
        m,
        policy(
            pose=PosePolicy(
                parent_frame_id="world", child_frame_id="ego", tolerance_ns=MS
            )
        ),
    )
    resolved = SceneSampleResolver(view, m).resolve_all()
    first = resolved[0]
    assert first.anchor.calibration is not None
    assert first.anchor.calibration.calibration_id == "cal-camera"
    lidar = first.member("LIDAR_TOP")
    assert lidar is not None and lidar.observation.observation_id == "lidar-1000000000"
    assert first.pose is not None and first.pose.pose_id == "pose-cam-1000000007"


def test_resolver_rejects_a_view_that_does_not_match_the_scene() -> None:
    m = manifest()
    view = build(m, policy())
    other = manifest(keyframe_timestamps_ns=(5_000_000_000,))
    with pytest.raises(SampleViewResolutionError):
        SceneSampleResolver(view, other).resolve_all()
