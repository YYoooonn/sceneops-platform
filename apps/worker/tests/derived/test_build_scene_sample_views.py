"""BUILD_SCENE_SAMPLE_VIEWS: Scenes + explicit policy + pinned label sets ->
immutable sample view revisions (ADR-007 §33.3)."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_worker.derived import DerivedManifestIntegrityError
from sceneops_worker.derived.resolution import resolve_sample_view
from tests.derived.flow_support import (
    T0,
    T1,
    build_views,
    detection_policy,
    import_label_set,
)
from tests.derived_harness import DATASET_ID, DATASET_VERSION, make_harness


async def _two_scene_harness(tmp_path):
    harness = make_harness(tmp_path)
    a = await harness.register_scene(robot_run_id="run-001")
    b = await harness.register_scene(robot_run_id="run-002")
    return harness, a, b


async def test_builds_one_pinned_view_per_scene(tmp_path) -> None:
    harness, a, b = await _two_scene_harness(tmp_path)
    result = await build_views(harness)

    assert [v.scene_id for v in result.views] == sorted([a.scene_id, b.scene_id])
    assert (result.scene_count, result.created_count, result.reused_count) == (2, 2, 0)
    # Per Scene: 2 keyframe samples; the sweep camera frame has no lidar in tolerance.
    assert (result.sample_count, result.dropped_anchor_count) == (4, 2)
    ref = next(v for v in result.views if v.scene_id == a.scene_id)

    record = await harness.artifact_record_store.get(ref.manifest_artifact_id)
    assert record.kind == ArtifactKind.SCENE_SAMPLE_VIEW_MANIFEST.value
    assert (record.owner_type, record.owner_id) == (
        ArtifactOwnerType.SCENE.value,
        a.scene_id,
    )
    assert (record.dataset_id, record.dataset_version) == (DATASET_ID, DATASET_VERSION)

    resolved = await resolve_sample_view(harness, ref)
    # The view pins the exact Scene revision it was built from.
    assert resolved.view.scene.manifest_checksum == a.manifest_checksum
    assert resolved.view.scene.manifest_artifact_id == a.manifest_artifact_id
    assert resolved.view.policy == detection_policy()
    assert [s.sample_id for s in resolved.view.samples] == ["smp-000000", "smp-000002"]
    assert [d.reasons for d in resolved.view.dropped] == [["missing_member:LIDAR_TOP"]]


async def test_rebuilding_converges_on_the_same_revisions(tmp_path) -> None:
    harness, _a, _b = await _two_scene_harness(tmp_path)
    first = await build_views(harness)
    second = await build_views(harness)
    assert second.views == first.views
    assert (second.created_count, second.reused_count) == (0, 2)


async def test_a_different_policy_is_a_different_revision(tmp_path) -> None:
    harness, _a, _b = await _two_scene_harness(tmp_path)
    first = await build_views(harness)
    second = await build_views(
        harness, policy=detection_policy(lidar_tolerance_ns=60_000_000)
    )
    assert {v.manifest_checksum for v in first.views}.isdisjoint(
        {v.manifest_checksum for v in second.views}
    )
    # Both revisions remain readable: nothing was overwritten.
    for ref in (*first.views, *second.views):
        await resolve_sample_view(harness, ref)


async def test_label_sets_are_pinned_and_attached(tmp_path) -> None:
    harness, a, _b = await _two_scene_harness(tmp_path)
    labels = await import_label_set(
        harness, "gt", run="run-001", covered=[T0, T1], labels={"car-0": (T0, 10.0)}
    )
    result = await build_views(harness, label_sets=[labels])
    ref = next(v for v in result.views if v.scene_id == a.scene_id)
    view = (await resolve_sample_view(harness, ref)).view

    assert view.label_sets == [labels]
    first, second = view.samples
    assert first.labels[0].covered and first.labels[0].label_ids == ["car-0"]
    assert (
        second.labels[0].covered and second.labels[0].label_ids == []
    )  # annotated negative
    # The other robot run's Scene is untouched by labels of run-001.
    other = next(v for v in result.views if v.scene_id != a.scene_id)
    other_view = (await resolve_sample_view(harness, other)).view
    assert all(not s.labels[0].covered for s in other_view.samples)


async def test_a_new_label_revision_is_a_new_view_revision(tmp_path) -> None:
    harness, a, _b = await _two_scene_harness(tmp_path)
    v1 = await import_label_set(
        harness, "gt", run="run-001", covered=[T0], labels={"c": (T0, 10.0)}
    )
    v2 = await import_label_set(
        harness, "gt", run="run-001", covered=[T0], labels={"c": (T0, 12.0)}
    )
    r1 = await build_views(harness, label_sets=[v1], scene_ids=[a.scene_id])
    r2 = await build_views(harness, label_sets=[v2], scene_ids=[a.scene_id])
    assert r1.views[0].manifest_checksum != r2.views[0].manifest_checksum


async def test_unregistered_or_tampered_label_pins_fail_loudly(tmp_path) -> None:
    harness, _a, _b = await _two_scene_harness(tmp_path)
    labels = await import_label_set(harness, "gt", run="run-001", covered=[T0])
    forged = labels.model_copy(update={"manifest_checksum": "sha256:" + "0" * 64})
    with pytest.raises(DerivedManifestIntegrityError):
        await build_views(harness, label_sets=[forged])


async def test_scenes_without_the_anchor_channel_are_skipped_with_a_reason(
    tmp_path,
) -> None:
    from sceneops_core.scenes.testing import build_scene_manifest, recording_source

    harness = make_harness(tmp_path)
    with_anchor = await harness.register_scene(robot_run_id="run-001")
    other_camera = build_scene_manifest(
        source=recording_source(robot_run_id="run-002"),
        camera_channel="CAM_OTHER",
        annotations_per_keyframe=0,
        payload_namespace="art-run-002",
    )
    without = await harness.register_scene(other_camera)

    result = await build_views(harness)
    assert [v.scene_id for v in result.views] == [with_anchor.scene_id]
    assert result.skipped == [
        {"scene_id": without.scene_id, "reason": "scene_lacks_anchor_channel"}
    ]

    # If no Scene qualifies the job fails instead of publishing nothing.
    only_other = make_harness(tmp_path / "other")
    await only_other.register_scene(other_camera)
    with pytest.raises(ValueError, match="no Scene produced samples"):
        await build_views(only_other)


async def test_unknown_scene_ids_are_rejected(tmp_path) -> None:
    harness, _a, _b = await _two_scene_harness(tmp_path)
    with pytest.raises(ValueError, match="not registered"):
        await build_views(harness, scene_ids=["scene-nope"])


async def test_empty_dataset_fails_loudly(tmp_path) -> None:
    harness = make_harness(tmp_path)
    with pytest.raises(ValueError, match="no registered scenes"):
        await build_views(harness)
