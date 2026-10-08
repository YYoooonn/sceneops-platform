"""Unit tests of the read-only equivalence verifier's decisions.

No store, no recording and no platform: the identity, timing and canonical
comparisons are pure over plain facts, the negative controls act on synthetic
loaded data, and the checksummed read runs over an in-memory store.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from sceneops_recording.equivalence import (
    ChannelContent,
    RecordingContent,
)

SCRIPT = Path(__file__).resolve().parents[1] / "streaming_equivalence_verify.py"
spec = importlib.util.spec_from_file_location("streaming_equivalence_verify", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

LOCKED = {
    "sha256": "sha256:locked",
    "message_count": 5,
    "topic_counts": {"/a": 3, "/b": 2},
}


def arm_request(run_id, checksum, size=10):
    return {
        "robot_run_id": run_id,
        "recording": {"uri": "s3://x", "checksum": checksum, "size_bytes": size},
    }


def facts(run_id, checksum, kind, size=10, counts=None):
    counts = LOCKED["topic_counts"] if counts is None else counts
    return {
        "run_id": run_id,
        "capture_source_kind": kind,
        "recording": {"checksum": checksum, "size_bytes": size},
        "message_count": sum(counts.values()),
        "channel_counts": dict(sorted(counts.items())),
    }


# ── Identity ─────────────────────────────────────────────────────────────────


def test_the_two_golden_runs_have_the_expected_identity():
    imported = identity_of("recording_import", "run-a", LOCKED["sha256"], "file")
    streamed = identity_of("streaming_acquisition", "run-b", "sha256:captured", "kafka")
    assert imported == [] and streamed == []


def identity_of(arm, run_id, checksum, kind, counts=None, manifest_checksum=None):
    return verify.identity_failures(
        arm,
        arm_request(run_id, checksum),
        facts(run_id, manifest_checksum or checksum, kind, counts=counts),
        LOCKED,
    )


def test_an_import_that_does_not_pin_the_locked_recording_fails():
    failures = identity_of("recording_import", "run-a", "sha256:other", "file")
    assert any("does not pin the locked recording" in f for f in failures)


def test_a_stream_that_pins_the_locked_recording_was_not_streamed():
    failures = identity_of("streaming_acquisition", "run-b", LOCKED["sha256"], "kafka")
    assert any("imported, not streamed" in f for f in failures)


@pytest.mark.parametrize(
    ("arm", "kind", "checksum"),
    [
        ("recording_import", "kafka", LOCKED["sha256"]),
        ("streaming_acquisition", "file", "sha256:captured"),
    ],
)
def test_the_capture_source_must_match_the_ingestion_mode(arm, kind, checksum):
    assert any("capture source" in f for f in identity_of(arm, "r", checksum, kind))


def test_counts_that_differ_from_the_lock_fail():
    failures = identity_of(
        "streaming_acquisition", "r", "sha256:c", "kafka", counts={"/a": 3, "/b": 1}
    )
    assert any("per-channel counts differ" in f for f in failures)


def test_a_registered_recording_that_differs_from_its_manifest_fails():
    failures = identity_of(
        "streaming_acquisition",
        "r",
        "sha256:c",
        "kafka",
        manifest_checksum="sha256:other",
    )
    assert any("differs from its manifest" in f for f in failures)


def test_a_manifest_of_another_run_fails():
    failures = verify.identity_failures(
        "recording_import",
        arm_request("run-a", LOCKED["sha256"]),
        facts("run-z", LOCKED["sha256"], "file"),
        LOCKED,
    )
    assert any("manifest is for run-z" in f for f in failures)


# ── Timing ───────────────────────────────────────────────────────────────────

NOW_NS = 1_900_000_000_000_000_000
SOURCE_NS = 1_530_000_000_000_000_000


def timings():
    imported = {
        "publish_equals_log": True,
        "first_log_time_ns": SOURCE_NS,
    }
    streamed = {
        "conforms": True,
        "violations": [],
        "all_channels_sequenced": True,
        "log_time_non_decreasing": True,
        "publish_not_after_log": True,
        "publish_equals_log": False,
        "first_publish_time_ns": verify.RECOGNIZABLY_NOT_NOW_NS + 1,
        "first_log_time_ns": verify.RECOGNIZABLY_NOT_NOW_NS + 2,
    }
    missions = {
        "recording_import": [SOURCE_NS, SOURCE_NS + 5],
        "streaming_acquisition": [SOURCE_NS, SOURCE_NS + 5],
    }
    return imported, streamed, missions


def test_a_proper_pair_of_timelines_has_no_timing_failure():
    assert verify.timing_failures(*timings()) == []


@pytest.mark.parametrize(
    ("side", "key", "value", "message"),
    [
        (1, "conforms", False, "not L1-conformant"),
        (1, "all_channels_sequenced", False, "no sequence"),
        (1, "log_time_non_decreasing", False, "log_time decreases"),
        (1, "publish_not_after_log", False, "later than its log_time"),
        (1, "publish_equals_log", True, "ingest time was lost"),
        (1, "first_publish_time_ns", SOURCE_NS, "not a wall-clock transport"),
        (1, "first_log_time_ns", SOURCE_NS, "not a wall-clock receive"),
        (0, "publish_equals_log", False, "imported publish_time differs"),
        (0, "first_log_time_ns", NOW_NS, "not the simulated source-timeline"),
    ],
)
def test_each_timing_property_is_enforced(side, key, value, message):
    parts = list(timings())
    parts[side] = {**parts[side], key: value}
    assert any(message in f for f in verify.timing_failures(*parts))


def test_mission_event_times_must_agree_and_stay_on_the_source_timeline():
    imported, streamed, missions = timings()
    missions["streaming_acquisition"] = [SOURCE_NS, NOW_NS]
    failures = verify.timing_failures(imported, streamed, missions)
    assert any("mission event times differ" in f for f in failures)
    assert any("wall-clock / replay time" in f for f in failures)


# ── Canonical comparison ─────────────────────────────────────────────────────


def unit(run_id, fingerprint, projection):
    manifest = SimpleNamespace(
        lineage=SimpleNamespace(
            source=SimpleNamespace(robot_run_id=run_id),
            producer=SimpleNamespace(producer_fingerprint=fingerprint),
        )
    )
    return manifest, projection


PROJECTION = {
    "observations": [{"timestamp_ns": 1, "payload": {"checksum": "sha256:x"}}]
}


def test_equal_content_from_two_runs_with_different_provenance_is_equivalent():
    a = {"recording": unit("run-a", "fp-a", PROJECTION)}
    b = {"recording": unit("run-b", "fp-b", copy.deepcopy(PROJECTION))}
    assert verify.canonical_failures("scene", a, b) == []


def test_a_semantic_difference_is_reported_where_it_is():
    changed = copy.deepcopy(PROJECTION)
    changed["observations"][0]["timestamp_ns"] += 1
    a = {"recording": unit("run-a", "fp-a", PROJECTION)}
    b = {"recording": unit("run-b", "fp-b", changed)}
    failures = verify.canonical_failures("scene", a, b)
    assert failures == [
        "scene recording: semantic content differs at $.observations[0].timestamp_ns: 1 vs 2"
    ]


def test_equality_of_one_run_with_itself_or_unchanged_provenance_is_vacuous():
    a = {"recording": unit("run-a", "fp", PROJECTION)}
    same_run = {"recording": unit("run-a", "fp-b", PROJECTION)}
    same_producer = {"recording": unit("run-b", "fp", PROJECTION)}
    assert any(
        "one RobotRun" in f for f in verify.canonical_failures("scene", a, same_run)
    )
    assert any(
        "vacuous" in f for f in verify.canonical_failures("scene", a, same_producer)
    )


def test_unit_keys_must_match_and_the_import_arm_must_have_units():
    a = {"recording": unit("run-a", "fp-a", PROJECTION)}
    b = {"other": unit("run-b", "fp-b", PROJECTION)}
    assert any(
        "unit keys differ" in f for f in verify.canonical_failures("scene", a, b)
    )
    assert any(
        "no recording_import episode manifests" in f
        for f in verify.canonical_failures("episode", {}, {})
    )


# ── Negative controls ────────────────────────────────────────────────────────


def channel(payloads):
    return ChannelContent(
        schema_name="std_msgs/msg/String",
        schema_encoding="ros2msg",
        message_encoding="cdr",
        message_count=len(payloads),
        payloads=Counter(payloads),
        sequenced_order=tuple(payloads),
    )


def loaded():
    content = RecordingContent(channels={"/a": channel(["h1", "h2", "h3"])})
    stamps = {"/a": [10, 20, 30]}
    scenes = {"recording": copy.deepcopy(PROJECTION)}
    observation = {"timestamp_ns": 5, "payload": {"checksum": "sha256:y"}}
    episodes = {
        "recording": {
            "observations": [copy.deepcopy(observation)],
            "states": [copy.deepcopy(observation)],
            "actions": [],
        }
    }
    return content, stamps, scenes, episodes


def test_every_minimal_perturbation_is_detected():
    controls = verify.negative_controls(*loaded())
    assert set(controls) == {
        "one message dropped from a channel",
        "one Header.stamp shifted by 1 ns",
        "Scene observation time shifted by 1 ns",
        "Scene observation payload checksum changed",
        "Episode observation time shifted by 1 ns",
        "Episode state time shifted by 1 ns",
        "Episode observation payload checksum changed",
    }
    assert all(controls.values())


def test_negative_controls_perturb_copies_and_leave_the_loaded_data_untouched():
    content, stamps, scenes, episodes = loaded()
    before = copy.deepcopy((content, stamps, scenes, episodes))
    verify.negative_controls(content, stamps, scenes, episodes)
    assert (content, stamps, scenes, episodes) == before


def test_a_comparison_blind_to_the_perturbation_is_reported_undetected(monkeypatch):
    monkeypatch.setattr(verify, "_differs", lambda a, b: False)
    controls = verify.negative_controls(*loaded())
    assert controls["one Header.stamp shifted by 1 ns"] is False
    assert controls["Scene observation payload checksum changed"] is False


# ── Reading a registered recording ───────────────────────────────────────────


class RangeStore:
    def __init__(self, data):
        self.data, self.ranges = data, []

    async def read_range(self, uri, offset, length):
        self.ranges.append((offset, length))
        return self.data[offset : offset + length]


def registered(data, checksum=None):
    return {
        "uri": "s3://bucket/recording.mcap",
        "size_bytes": len(data),
        "checksum": checksum or f"sha256:{hashlib.sha256(data).hexdigest()}",
    }


def test_a_recording_is_read_in_bounded_ranges_and_hash_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "READ_CHUNK_BYTES", 4)
    data = bytes(range(10))
    store = RangeStore(data)
    destination = tmp_path / "r.mcap"
    asyncio.run(verify.materialize_recording(store, registered(data), destination))
    assert destination.read_bytes() == data
    assert store.ranges == [(0, 4), (4, 4), (8, 2)]


def test_a_recording_that_does_not_hash_to_its_registration_is_refused(tmp_path):
    data = b"recorded"
    with pytest.raises(verify.RegisteredRecordingError, match="registered checksum"):
        asyncio.run(
            verify.materialize_recording(
                RangeStore(data),
                registered(data, checksum="sha256:" + "0" * 64),
                tmp_path / "r.mcap",
            )
        )
