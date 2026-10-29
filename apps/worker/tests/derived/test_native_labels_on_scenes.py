"""Native nuScenes labels resolved on the nuScenes Scene the same content
builds (ADR-015 §9, M3.0 OQ-6; M3 plan S6 acceptance 1–3), without storage:
the Scene builder, the native label importer, the anchor resolver and sample
views, on the raw scene-0061 slice. Skips without data/raw/nuscenes; the
platform run is `make test-infrastructure SUITE=nuscenes-labels`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sceneops_core.labels import LabelSetRef, SceneAnchorResolver
from sceneops_core.common.derived_ids import label_set_artifact_id
from sceneops_core.provenance import PinnedContent
from sceneops_core.sample_views import (
    MemberPolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
    SceneRevisionRef,
    build_scene_sample_view,
)
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.sources import SnapshotSelection
from sceneops_derived import nuscenes_labels
from sceneops_scenes import nuscenes_builder
from sceneops_sources import LocalContentFiles, build_inventory, select_files

REPO_ROOT = Path(__file__).resolve().parents[4]
NUSCENES = REPO_ROOT / "data/raw/nuscenes"
SCENE_0061 = "cc8c0bf57f984915a77078b10eb33198"


def test_labels_anchor_on_the_scene_builders_clock_frame_and_channel():
    assert nuscenes_labels.ANCHOR_CLOCK == nuscenes_builder.NUSCENES_CLOCK
    assert nuscenes_labels.BOX_FRAME == nuscenes_builder.WORLD_FRAME


@pytest.fixture(scope="module")
def scene_and_labels():
    if not (NUSCENES / "v1.0-mini" / "scene.json").is_file():
        pytest.skip("data/raw/nuscenes is missing")
    include = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"]["nuscenes-scene-0061"]["include"]
    config = NuScenesSceneBuildConfig.model_validate(
        json.loads(
            (
                REPO_ROOT
                / "config/reference/nuscenes-scene-0061/scene_build_config.json"
            ).read_text()
        )
    )
    inventory = build_inventory(
        NUSCENES, select_files(NUSCENES, SnapshotSelection(include=tuple(include)))
    )
    pinned = PinnedContent(inventory=inventory)
    files = LocalContentFiles(NUSCENES, inventory)
    (planned,) = nuscenes_builder.plan_nuscenes_scenes(
        pinned=pinned, files=files, config=config, unit_keys=[SCENE_0061]
    ).scenes
    labels = nuscenes_labels.nuscenes_label_set(
        pinned=pinned, files=files, label_set_id="nuscenes-scene-0061",
        unit_keys=[SCENE_0061],
    )  # fmt: skip
    return planned.manifest, labels


def test_every_anchor_resolves_inside_its_keyframe_group(scene_and_labels):
    scene, labels = scene_and_labels
    resolver = SceneAnchorResolver(scene)
    groups = {g.group_id: g for g in scene.groups}
    channel = {o.observation_id: o.channel for o in scene.observations}
    resolved = [resolver.resolve(a) for a in labels.coverage]
    assert len(resolved) == 39 and None not in resolved
    for anchor, where in zip(labels.coverage, resolved, strict=True):
        assert channel[where.observation_id] == "LIDAR_TOP"
        assert groups[where.group_id].native_ref == anchor.group_ref
    assert len({r.group_id for r in resolved}) == 39
    assert len(labels.labels) == 4699


def _view(scene, labels, anchor: str, member: str):
    checksum = labels.checksum()
    ref = LabelSetRef(
        label_set_id=labels.label_set_id,
        manifest_artifact_id=label_set_artifact_id(
            label_set_id=labels.label_set_id, checksum=checksum
        ),
        manifest_checksum=checksum,
    )
    return build_scene_sample_view(
        scene=SceneRevisionRef(
            scene_id="scene-x", manifest_artifact_id="art-x",
            manifest_checksum=scene.checksum(),
        ),
        manifest=scene,
        policy=SampleViewPolicy(
            anchor=SampleAnchorPolicy(channel=anchor),
            members=[MemberPolicy(channel=member, tolerance_ns=50_000_000, required=False)],
        ),
        label_sets=[(ref, labels)],
    )  # fmt: skip


def test_camera_and_lidar_views_present_the_same_labels_per_group(scene_and_labels):
    scene, labels = scene_and_labels
    before = labels.to_canonical_bytes()
    group_of = {o: g.native_ref for g in scene.groups for o in g.observation_ids}
    per_view = []
    for anchor, member in (("CAM_FRONT", "LIDAR_TOP"), ("LIDAR_TOP", "CAM_FRONT")):
        view = _view(scene, labels, anchor, member)
        (stats,) = view.label_stats
        assert stats.attached_label_count + stats.unattached_label_count == 4699
        found = {}
        for sample in view.samples:
            if sample.labels[0].covered:
                (group,) = {
                    group_of[o]
                    for o in sample.member_observation_ids()
                    if o in group_of
                }
                found[group] = sample.labels[0].label_ids
        per_view.append(found)
    camera, lidar = per_view
    assert camera == lidar and len(camera) == 39
    assert sum(map(len, camera.values())) == 4699
    # Building views never changes the label set.
    assert labels.to_canonical_bytes() == before
