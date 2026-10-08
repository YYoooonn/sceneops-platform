"""Identity versus semantic equivalence of recording-derived Scenes
(ADR-007 §29.12 / I-35, §30.5, §30.9).

Same RobotRun rebuilds are fully deterministic: Scene ids, unit keys,
manifest bytes and payload artifact ids. Two semantically equivalent
recordings of different acquisitions (other robot_run_id, other recorder
log_time, other cross-channel write order) keep distinct provenance-owned
artifact ids, but their canonical semantic content is equal when
canonical time and segmentation come from source-semantic timestamps.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import semantic_scene_content
from sceneops_scenes.recording_builder import (
    RecordingRevision,
    iter_planned_payloads,
    plan_recording_scenes,
)

sys.path.insert(0, str(Path(__file__).parent))
from sceneops_recording.testing.ros2_recordings import (  # noqa: E402
    CAMERA_TOPIC,
    JPEG,
    build_config,
    compressed_image,
    default_recording,
)

BATCH = RecordingRevision("run-batch", "sha256:" + "a" * 64, "mcap_log_time")
REPLAY = RecordingRevision("run-replay", "sha256:" + "b" * 64, "mcap_log_time")


def _plan(path, revision, config=None):
    return plan_recording_scenes(
        path,
        revision=revision,
        config=RecordingSceneBuildConfig.model_validate(config or build_config()),
    )


def _payload_ids(plan):
    return {key: p.artifact_id for key, p in plan.payloads.items()}


def _bytes_by_observation(path, plan, config=None):
    cfg = RecordingSceneBuildConfig.model_validate(config or build_config())
    data = {p.artifact_id: b for p, b in iter_planned_payloads(path, plan, cfg)}
    return {
        o.observation_id: data[o.payload.artifact_id]
        for s in plan.scenes
        for o in s.manifest.observations
    }


def _replayed(rec, *, receive_delay_ns: int):
    """The same source messages as another acquisition would record them:
    a different recorder receive time (log_time / publish_time) and a
    different interleaving of topics that share a receive time. Per-topic
    order and every payload byte are unchanged."""
    replay = copy.deepcopy(rec)
    for index, message in enumerate(replay.messages):
        message.log_time += receive_delay_ns + index  # monotone jitter
        message.publish_time = message.log_time
    # Per frame the fixture writes /tf, CameraInfo, image, lidar; swap each
    # CameraInfo with its image (same receive time), which moves every image
    # to another global file position without changing per-topic order.
    order = list(range(len(replay.messages)))
    for i in range(2, len(order) - 2, 4):
        order[i], order[i + 1] = order[i + 1], order[i]
    return replay, order


# --- A. same RobotRun, same configuration ------------------------------------------


def test_same_run_rebuild_is_identical(tmp_path):
    path = default_recording().write(tmp_path / "r.mcap")
    a, b = _plan(path, BATCH), _plan(path, BATCH)

    assert [s.unit_key for s in a.scenes] == [s.unit_key for s in b.scenes]
    assert [s.manifest.to_canonical_bytes() for s in a.scenes] == [
        s.manifest.to_canonical_bytes() for s in b.scenes
    ]
    assert _payload_ids(a) == _payload_ids(b)
    ids = [
        scene_id_for(
            dataset_id="d", dataset_version="v1", source=s.manifest.lineage.source
        )
        for s in a.scenes
    ]
    assert ids == [
        scene_id_for(
            dataset_id="d", dataset_version="v1", source=s.manifest.lineage.source
        )
        for s in b.scenes
    ]


def test_same_run_identity_ignores_cross_channel_write_order(tmp_path):
    """D: re-interleaving topics leaves every per-topic occurrence, hence
    every payload id and the manifests, unchanged."""
    rec = default_recording()
    _, order = _replayed(rec, receive_delay_ns=0)
    a = _plan(rec.write(tmp_path / "a.mcap"), BATCH)
    b = _plan(rec.write(tmp_path / "b.mcap", order=order, chunk_size=256), BATCH)
    assert order != sorted(order)
    assert _payload_ids(a) == _payload_ids(b)
    assert [s.manifest.to_canonical_bytes() for s in a.scenes] == [
        s.manifest.to_canonical_bytes() for s in b.scenes
    ]


# --- B. same RobotRun, other configuration -------------------------------------------


def test_payload_identity_is_configuration_independent(tmp_path):
    """Segmentation and time-policy changes reuse every payload artifact,
    and no id can name different bytes under two configurations."""
    path = default_recording().write(tmp_path / "r.mcap")
    base = _plan(path, BATCH)
    variants = [
        _plan(path, BATCH, build_config(duration_ns=10_000_000_000)),
        _plan(
            path,
            BATCH,
            build_config(
                clock="mcap_log_time",
                camera_time=("log_time", "mcap_log_time"),
                lidar_time=("publish_time", "mcap_publish_time"),
                pose_time=("log_time", "mcap_log_time"),
            ),
        ),
    ]
    for variant in variants:
        assert (
            variant.producer.producer_fingerprint != base.producer.producer_fingerprint
        )
        assert _payload_ids(variant) == _payload_ids(base)
        assert {
            p.artifact_id: (p.checksum, p.size_bytes) for p in variant.payloads.values()
        } == {p.artifact_id: (p.checksum, p.size_bytes) for p in base.payloads.values()}


def test_duplicate_occurrences_keep_distinct_identities(tmp_path):
    """Two occurrences with identical bytes and timestamp are two
    observations and two payload artifacts: no content deduplication."""
    rec = default_recording(frame_stamps_ns=(1_000_000_000,))
    for _ in range(2):
        rec.add(
            CAMERA_TOPIC,
            "sensor_msgs/msg/CompressedImage",
            compressed_image(1_200_000_000, data=JPEG + b"dup"),
            1_300_000_000,
        )
    plan = _plan(rec.write(tmp_path / "r.mcap"), BATCH)
    duplicates = [
        o
        for s in plan.scenes
        for o in s.manifest.observations
        if o.channel == CAMERA_TOPIC and o.timestamp_ns == 1_200_000_000
    ]
    assert len(duplicates) == 2
    assert len({o.observation_id for o in duplicates}) == 2
    assert len({o.payload.artifact_id for o in duplicates}) == 2
    assert len({o.payload.checksum for o in duplicates}) == 1


# --- C. different, semantically equivalent acquisitions ------------------------------


def test_equivalent_recordings_differ_only_in_provenance(tmp_path):
    rec = default_recording()
    replay, order = _replayed(rec, receive_delay_ns=37_000_000)
    batch_path = rec.write(tmp_path / "batch.mcap")
    replay_path = replay.write(tmp_path / "replay.mcap", order=order, chunk_size=512)
    assert batch_path.read_bytes() != replay_path.read_bytes()

    a, b = _plan(batch_path, BATCH), _plan(replay_path, REPLAY)

    # Canonical semantic content is equal, Scene by Scene.
    assert [s.unit_key for s in a.scenes] == [s.unit_key for s in b.scenes]
    assert [semantic_scene_content(s.manifest) for s in a.scenes] == [
        semantic_scene_content(s.manifest) for s in b.scenes
    ]
    # ... and so are the logical payload bytes behind each observation.
    assert _bytes_by_observation(batch_path, a) == _bytes_by_observation(replay_path, b)

    # Provenance and provenance-owned identities differ.
    for x, y in zip(a.scenes, b.scenes):
        sx, sy = x.manifest.lineage.source, y.manifest.lineage.source
        assert (sx.robot_run_id, sy.robot_run_id) == ("run-batch", "run-replay")
        assert sx.recording_checksum != sy.recording_checksum
        assert (sx.start_timestamp_ns, sx.end_timestamp_ns) == (
            sy.start_timestamp_ns,
            sy.end_timestamp_ns,
        )
        assert x.manifest.checksum() != y.manifest.checksum()
    assert a.producer.producer_fingerprint != b.producer.producer_fingerprint
    assert not set(_payload_ids(a).values()) & set(_payload_ids(b).values())


def test_receive_time_segmentation_is_outside_equivalence(tmp_path):
    """Segmenting on mcap_log_time makes windows depend on the acquisition
    (§29.12): the windows move with the recorder's receive time while the
    observations keep their source timestamps."""
    rec = default_recording()
    replay, _ = _replayed(rec, receive_delay_ns=37_000_000)
    config = build_config(clock="mcap_log_time")
    a = _plan(rec.write(tmp_path / "a.mcap"), BATCH, config)
    b = _plan(replay.write(tmp_path / "b.mcap"), REPLAY, config)

    starts_a = [s.manifest.lineage.source.start_timestamp_ns for s in a.scenes]
    starts_b = [s.manifest.lineage.source.start_timestamp_ns for s in b.scenes]
    assert starts_a != starts_b
    observed = [
        sorted(
            (o.channel, o.timestamp_ns)
            for s in plan.scenes
            for o in s.manifest.observations
        )
        for plan in (a, b)
    ]
    assert observed[0] == observed[1]
