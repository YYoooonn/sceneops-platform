"""The nuScenes Scene builder (ADR-015 §6.2, §6.3, §6.5–§6.7; M3.0 D2, D3,
D6, D7).

Synthetic v1.0 trees exercise each rule and each refusal; expectations are
the values the tree was written with. The raw ``scene-0061`` slice is held to
the numbers of the M3 plan (S4 acceptance), measured on the raw data in M3.0;
those tests skip without data/raw/nuscenes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sceneops_core.provenance import ClockOrigin, PinnedContent
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.scenes.schemas import (
    SceneFrameRole,
    SceneGroupKind,
    SceneModality,
    load_canonical_scene_manifest,
    scene_id_for,
)
from sceneops_core.sources import SnapshotSelection
from sceneops_scenes.nuscenes_builder import (
    NUSCENES_CLOCK,
    NuScenesSceneBuildError,
    plan_nuscenes_scenes,
)
from sceneops_sources import LocalContentFiles, build_inventory, select_files
from sceneops_sources.nuscenes_synthetic import (
    BASE_US,
    CAMERA_K,
    CHANNELS,
    INCLUDE,
    KEY_FRAME_OFFSET_US,
    VERSION,
    token,
    write_nuscenes_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
NUSCENES = REPO_ROOT / "data/raw/nuscenes"
SCENE_0061 = "cc8c0bf57f984915a77078b10eb33198"
ALL_CHANNELS = sorted(CHANNELS)
CONFIG = NuScenesSceneBuildConfig(channels=ALL_CHANNELS)
MEDIA_TYPES = {
    "camera": "image/jpeg",
    "lidar": "application/x.nuscenes.lidar-pcd-bin",
    "radar": "application/x.nuscenes.radar-pcd",
}


def _inventory(root: Path, include=INCLUDE):
    return build_inventory(
        root, select_files(root, SnapshotSelection(include=tuple(include)))
    )


def _plan(root: Path, *, include=INCLUDE, config=CONFIG, unit_keys=None):
    inventory = _inventory(root, include)
    return plan_nuscenes_scenes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(root, inventory),
        config=config,
        unit_keys=unit_keys,
    )


def _scene(plan, s: int):
    return next(p.manifest for p in plan.scenes if p.unit_key == token("scene", s))


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# --- rules ---------------------------------------------------------------------------


def test_one_scene_per_native_scene_with_every_reading_of_its_samples(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=2, samples=3, sweeps=1)
    plan = _plan(tmp_path)

    assert [s.unit_key for s in plan.scenes] == sorted(
        [token("scene", 0), token("scene", 1)]
    )
    assert plan.payloads == {}
    manifest = _scene(plan, 1)
    source = manifest.lineage.source
    assert (source.source_kind, source.unit_key) == ("content", token("scene", 1))
    assert scene_id_for(source).startswith("scene-")
    # 3 samples x 3 channels x (key frame + 1 sweep).
    assert len(manifest.observations) == 18
    readings = sorted(
        BASE_US + 10**8 + k * 500_000 + KEY_FRAME_OFFSET_US[c] + w * 100_000
        for k in range(3)
        for c in CHANNELS
        for w in range(2)
    )
    assert sorted(o.timestamp_ns for o in manifest.observations) == [
        us * 1000 for us in readings
    ]
    assert (source.source_clock, source.start_timestamp_ns, source.end_timestamp_ns) == (
        NUSCENES_CLOCK, readings[0] * 1000, readings[-1] * 1000 + 1,
    )  # fmt: skip
    (domain,) = manifest.clock_domains
    assert (domain.clock, domain.origin, domain.resolution_ns) == (
        NUSCENES_CLOCK, ClockOrigin.UNIX_EPOCH, 1000,
    )  # fmt: skip
    assert [(c.channel, c.modality, c.frame_id, c.source_clock) for c in manifest.channels] == [
        (c, SceneModality(CHANNELS[c][0]), c, NUSCENES_CLOCK) for c in ALL_CHANNELS
    ]  # fmt: skip
    assert {(f.frame_id, f.role) for f in manifest.coordinate_frames} == {
        ("global", SceneFrameRole.WORLD),
        ("ego", SceneFrameRole.EGO),
        *((c, SceneFrameRole.SENSOR) for c in CHANNELS),
    }
    camera = [o for o in manifest.observations if o.channel == "CAM_FRONT"]
    assert [o.observation_id for o in camera] == [
        f"CAM_FRONT-{i:08d}" for i in range(6)
    ]
    assert {o.native_ref for o in camera} == {
        token("sample_data", 1, k, "CAM_FRONT", w) for k in range(3) for w in range(2)
    }
    assert {(o.image_size.width_px, o.image_size.height_px) for o in camera} == {
        (1600, 900)
    }
    assert all(
        o.image_size is None for o in manifest.observations if o.channel != "CAM_FRONT"
    )


def test_payloads_are_the_preserved_files_with_their_inventory_integrity(tmp_path):
    write_nuscenes_tree(tmp_path)
    plan = _plan(tmp_path)
    content_id = _inventory(tmp_path).content_id
    for manifest in (p.manifest for p in plan.scenes):
        modality = {c.channel: c.modality.value for c in manifest.channels}
        for o in manifest.observations:
            ref = o.payload
            assert ref.kind == "source_file" and ref.content_id == content_id
            assert ref.frame is None
            assert ref.media_type == MEDIA_TYPES[modality[o.channel]]
            assert ref.sha256 == _sha256(tmp_path / ref.path)
            assert ref.size_bytes == (tmp_path / ref.path).stat().st_size
            assert ref.path.endswith(CHANNELS[o.channel][2])


def test_each_reading_links_its_own_calibration_and_ego_pose(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=1, samples=2, sweeps=1)
    manifest = _scene(_plan(tmp_path), 0)
    calibrations = {c.calibration_id: c for c in manifest.calibrations}
    assert set(calibrations) == {
        f"cal-{token('calibrated_sensor', 0, c)}" for c in CHANNELS
    }
    camera = calibrations[f"cal-{token('calibrated_sensor', 0, 'CAM_FRONT')}"]
    assert camera.camera_intrinsic == tuple(tuple(r) for r in CAMERA_K)
    assert (camera.extrinsic.parent_frame_id, camera.extrinsic.child_frame_id) == (
        "ego",
        "CAM_FRONT",
    )
    assert camera.extrinsic.translation_m == (1.5, 0.0, 1.75)
    radar = calibrations[f"cal-{token('calibrated_sensor', 0, 'RADAR_FRONT')}"]
    assert radar.camera_intrinsic is None

    poses = {p.pose_id: p for p in manifest.poses}
    assert len(poses) == len(manifest.observations)
    for o in manifest.observations:
        assert o.calibration_id == f"cal-{token('calibrated_sensor', 0, o.channel)}"
        # The synthetic tree, like nuScenes, gives each reading its own ego pose
        # token, equal to the reading's token, at the reading's timestamp.
        pose = poses[o.ego_pose_id]
        assert o.ego_pose_id == f"pose-{o.native_ref}"
        assert pose.timestamp_ns == o.timestamp_ns
        assert (pose.transform.parent_frame_id, pose.transform.child_frame_id) == (
            "global",
            "ego",
        )
        assert pose.transform.rotation_wxyz == (0.6, 0.0, 0.0, -0.8)


def test_keyframe_groups_are_the_samples_with_their_key_frames(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=1, samples=3, sweeps=2)
    manifest = _scene(_plan(tmp_path), 0)
    by_id = {o.observation_id: o for o in manifest.observations}
    assert [g.native_ref for g in manifest.groups] == [
        token("sample", 0, k) for k in range(3)
    ]
    for k, group in enumerate(manifest.groups):
        assert group.group_id == f"keyframe-{k:06d}"
        assert group.kind == SceneGroupKind.KEYFRAME
        assert group.timestamp_ns == (BASE_US + k * 500_000) * 1000
        assert {by_id[i].native_ref for i in group.observation_ids} == {
            token("sample_data", 0, k, c, 0) for c in CHANNELS
        }


def test_channel_selection_keeps_the_source_window(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=1)
    everything = _scene(_plan(tmp_path), 0)
    lidar = _scene(
        _plan(tmp_path, config=NuScenesSceneBuildConfig(channels=["LIDAR_TOP"])), 0
    )
    assert lidar.lineage.source == everything.lineage.source
    assert {o.channel for o in lidar.observations} == {"LIDAR_TOP"}
    assert all(len(g.observation_ids) == 1 for g in lidar.groups)
    assert (
        lidar.lineage.producer.producer_fingerprint
        != everything.lineage.producer.producer_fingerprint
    )


def test_a_subset_builds_the_same_revisions(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=2)
    full = _plan(tmp_path)
    one = _plan(tmp_path, unit_keys=[token("scene", 1)])
    assert [s.unit_key for s in one.scenes] == [token("scene", 1)]
    assert one.scenes[0].manifest.checksum() == _scene(full, 1).checksum()


def test_build_is_deterministic_canonical_and_free_of_declaration_facts(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=1)
    first, second = _plan(tmp_path), _plan(tmp_path)
    data = first.scenes[0].manifest.to_canonical_bytes()
    assert data == second.scenes[0].manifest.to_canonical_bytes()
    assert load_canonical_scene_manifest(data) == first.scenes[0].manifest
    # scene.name is display data; no snapshot, upstream, version or licence.
    for fact in ("scene-0001", VERSION, "nuscenes-terms", "snapshot"):
        assert fact.encode() not in data


# --- refusals --------------------------------------------------------------------------


def test_an_unknown_unit_key_is_refused(tmp_path):
    write_nuscenes_tree(tmp_path)
    with pytest.raises(NuScenesSceneBuildError, match="name no scene"):
        _plan(tmp_path, unit_keys=[token("scene", 9)])


def test_a_scene_whose_files_are_not_preserved_is_refused(tmp_path):
    write_nuscenes_tree(tmp_path, scenes=2, omit_scene_files=(0,))
    with pytest.raises(NuScenesSceneBuildError, match=token("scene", 0)):
        _plan(tmp_path)
    with pytest.raises(NuScenesSceneBuildError, match="not files of"):
        _plan(tmp_path, unit_keys=[token("scene", 0)])
    assert len(_plan(tmp_path, unit_keys=[token("scene", 1)]).scenes) == 1


def test_an_unknown_channel_is_refused(tmp_path):
    write_nuscenes_tree(tmp_path)
    with pytest.raises(NuScenesSceneBuildError, match="not sensor channels"):
        _plan(tmp_path, config=NuScenesSceneBuildConfig(channels=["CAM_BACK"]))


def _first(table: str, where):
    def find(tables):
        return next(row for row in tables[table] if where(row))

    return find


def _sensor_token(channel: str) -> str:
    return token("sensor", channel)


def _reading_of(channel: str, *, key_frame: bool = True):
    cs = {token("calibrated_sensor", s, channel) for s in range(2)}
    return _first(
        "sample_data",
        lambda r: r["calibrated_sensor_token"] in cs and r["is_key_frame"] == key_frame,
    )


def _set(find, **values):
    def mutate(tables):
        find(tables).update(values)

    return mutate


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            _set(_reading_of("CAM_FRONT"), fileformat="png"),
            "the evidence for image/jpeg",
        ),
        (
            _set(
                _first("sensor", lambda r: r["channel"] == "RADAR_FRONT"),
                modality="sonar",
            ),
            "only camera, lidar and radar",
        ),
        (
            _set(
                _first(
                    "calibrated_sensor",
                    lambda r: r["sensor_token"] == _sensor_token("CAM_FRONT"),
                ),
                camera_intrinsic=[],
            ),
            "no camera_intrinsic",
        ),
        (
            _set(
                _first(
                    "calibrated_sensor",
                    lambda r: r["sensor_token"] == _sensor_token("LIDAR_TOP"),
                ),
                camera_intrinsic=CAMERA_K,
            ),
            "camera_intrinsic for a lidar",
        ),
        (_set(_reading_of("CAM_FRONT"), width=0), "no image size"),
        (_set(_reading_of("LIDAR_TOP"), width=8), "has an image size"),
        (
            _set(_reading_of("RADAR_FRONT", key_frame=False), is_key_frame=True),
            "more than one key frame",
        ),
        (
            _set(_first("ego_pose", lambda r: True), timestamp=BASE_US - 10**6),
            "outside the unit window",
        ),
        (
            _set(
                _first("calibrated_sensor", lambda r: True),
                rotation=[2.0, 0.0, 0.0, 0.0],
            ),
            "unit quaternion",
        ),
    ],
)
def test_content_the_manifest_cannot_represent_faithfully_is_refused(
    tmp_path, mutate, match
):
    write_nuscenes_tree(tmp_path, scenes=1, sweeps=1, mutate_tables=mutate)
    with pytest.raises(NuScenesSceneBuildError, match=match):
        _plan(tmp_path)


def test_an_unsupported_table_version_is_refused(tmp_path):
    write_nuscenes_tree(tmp_path, version="v2.0-mini")
    with pytest.raises(NuScenesSceneBuildError, match="not supported"):
        _plan(tmp_path, include=("**",))


def test_the_config_rejects_a_repeated_channel():
    with pytest.raises(ValueError, match="more than once"):
        NuScenesSceneBuildConfig(channels=["LIDAR_TOP", "LIDAR_TOP"])
    assert NuScenesSceneBuildConfig(channels=["b", "a"]).normalized() == {
        "channels": ["a", "b"]
    }


# --- the raw scene-0061 slice ------------------------------------------------------------


@pytest.fixture(scope="module")
def slice_0061():
    if not (NUSCENES / "v1.0-mini" / "scene.json").is_file():
        pytest.skip("data/raw/nuscenes is missing")
    snapshots = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"]
    include = snapshots["nuscenes-scene-0061"]["include"]
    config = NuScenesSceneBuildConfig.model_validate(
        json.loads(
            (
                REPO_ROOT
                / "config/reference/nuscenes-scene-0061/scene_build_config.json"
            ).read_text()
        )
    )
    inventory = _inventory(NUSCENES, include)
    files = LocalContentFiles(NUSCENES, inventory)
    return inventory, files, config


def test_scene_0061_counts_window_and_media_types(slice_0061):
    inventory, files, config = slice_0061
    plan = plan_nuscenes_scenes(
        pinned=PinnedContent(inventory=inventory),
        files=files,
        config=config,
        unit_keys=[SCENE_0061],
    )
    (planned,) = plan.scenes
    manifest = planned.manifest
    modality = {c.channel: c.modality.value for c in manifest.channels}
    assert len(manifest.channels) == 12
    counts: dict[str, int] = {}
    for o in manifest.observations:
        counts[modality[o.channel]] = counts.get(modality[o.channel], 0) + 1
    assert counts == {"camera": 1318, "lidar": 382, "radar": 1263}
    assert [len(g.observation_ids) for g in manifest.groups] == [12] * 39
    assert len(manifest.poses) == 2963
    source = manifest.lineage.source
    assert (source.start_timestamp_ns, source.end_timestamp_ns) == (
        1532402927604844000,
        1532402946826796001,
    )
    first_sample = manifest.groups[0].timestamp_ns
    last_sample = manifest.groups[-1].timestamp_ns
    assert first_sample - source.start_timestamp_ns == 43_107_000
    assert source.end_timestamp_ns - 1 - last_sample == 29_279_000
    assert {o.payload.media_type for o in manifest.observations} == set(
        MEDIA_TYPES.values()
    )


def test_scene_0061_slice_without_unit_keys_names_an_unpreserved_scene(slice_0061):
    inventory, files, config = slice_0061
    with pytest.raises(NuScenesSceneBuildError, match="must be preserved"):
        plan_nuscenes_scenes(
            pinned=PinnedContent(inventory=inventory), files=files, config=config
        )
