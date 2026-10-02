"""Contract tests for ProducerInfo, build_config normalization and the
producer fingerprint (ADR-007 §15, §27.7)."""

from __future__ import annotations

import hashlib
from enum import StrEnum

import pytest
from pydantic import ValidationError

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.provenance import (
    PRODUCER_FINGERPRINT_SCHEMA_V1,
    ExternalSourceRevision,
    ProducerFingerprintMismatchError,
    ProducerInfo,
    RecordingSourceRevision,
    compute_producer_fingerprint,
    normalize_build_config,
)

PRODUCER_ID = "sceneops.recording_scene_builder"
RECORDING = RecordingSourceRevision(
    robot_run_id="run-001", recording_checksum="sha256:" + "a" * 64
)
EXTERNAL = ExternalSourceRevision(
    format="nuscenes", format_version="v1.0-mini", external_revision="r1", checksum=None
)
CONFIG = {
    "selected_channels": ["/camera/front/image", "/lidar/top", "/odom"],
    "segmentation": {"policy": "fixed_window", "window_ns": 20_000_000_000},
}


def _fingerprint(**overrides) -> str:
    fields = {
        "producer_id": PRODUCER_ID,
        "semantics_version": 1,
        "build_config": CONFIG,
        "source": RECORDING,
    }
    fields.update(overrides)
    return compute_producer_fingerprint(**fields)


def test_fingerprint_matches_frozen_definition():
    expected_payload = {
        "fingerprint_schema": PRODUCER_FINGERPRINT_SCHEMA_V1,
        "producer_id": PRODUCER_ID,
        "semantics_version": 1,
        "build_config": CONFIG,
        "source": {
            "source_kind": "recording",
            "robot_run_id": "run-001",
            "recording_checksum": "sha256:" + "a" * 64,
        },
    }
    expected = (
        "sha256:" + hashlib.sha256(canonical_json_bytes(expected_payload)).hexdigest()
    )
    assert _fingerprint() == expected


def test_same_source_config_and_version_give_same_fingerprint():
    reordered_keys = {
        "segmentation": {"window_ns": 20_000_000_000, "policy": "fixed_window"},
        "selected_channels": ["/camera/front/image", "/lidar/top", "/odom"],
    }
    assert _fingerprint() == _fingerprint(build_config=reordered_keys)
    assert _fingerprint() == _fingerprint(
        source=RecordingSourceRevision.model_validate(RECORDING.model_dump(mode="json"))
    )


def test_external_source_location_does_not_affect_fingerprint():
    # ExternalSourceRevision cannot even carry uri / external_name.
    with pytest.raises(ValidationError):
        ExternalSourceRevision(**EXTERNAL.model_dump(), uri="/data/raw/nuscenes")


@pytest.mark.parametrize(
    "overrides",
    [
        # excluding a whole channel is a boundary decision
        {
            "build_config": {
                **CONFIG,
                "selected_channels": ["/camera/front/image", "/odom"],
            }
        },
        {
            "build_config": {
                **CONFIG,
                "segmentation": {"policy": "fixed_window", "window_ns": 1},
            }
        },
        {"semantics_version": 2},
        {"producer_id": "sceneops.recording_episode_builder"},
        {
            "source": RecordingSourceRevision(
                robot_run_id="run-001", recording_checksum="sha256:" + "b" * 64
            )
        },
        {
            "source": RecordingSourceRevision(
                robot_run_id="run-002", recording_checksum="sha256:" + "a" * 64
            )
        },
        {"source": EXTERNAL},
    ],
)
def test_semantic_input_changes_change_the_fingerprint(overrides):
    assert _fingerprint(**overrides) != _fingerprint()


@pytest.mark.parametrize(
    "revision",
    [
        ExternalSourceRevision(
            format="nuscenes",
            format_version="v1.0-trainval",
            external_revision="r1",
            checksum=None,
        ),
        ExternalSourceRevision(
            format="nuscenes",
            format_version="v1.0-mini",
            external_revision="r2",
            checksum=None,
        ),
        ExternalSourceRevision(
            format="nuscenes",
            format_version="v1.0-mini",
            external_revision="r1",
            checksum="sha256:" + "c" * 64,
        ),
    ],
)
def test_different_external_source_revision_changes_the_fingerprint(revision):
    assert _fingerprint(source=revision) != _fingerprint(source=EXTERNAL)


def test_channel_order_is_semantic_unless_the_producer_sorts():
    # The core does not guess which lists are unordered; producers normalize
    # unordered collections (e.g. selected channels) to sorted lists.
    reversed_channels = {
        **CONFIG,
        "selected_channels": list(reversed(CONFIG["selected_channels"])),
    }
    assert _fingerprint(build_config=reversed_channels) != _fingerprint()


@pytest.mark.parametrize(
    "key", ["job_id", "pipeline_run_id", "execution_id", "generated_at", "created_at"]
)
def test_execution_scoped_values_cannot_enter_the_fingerprint(key):
    with pytest.raises(ValueError, match="execution-scoped"):
        _fingerprint(build_config={**CONFIG, key: "job-123"})
    with pytest.raises(ValueError, match="execution-scoped"):
        _fingerprint(build_config={**CONFIG, "segmentation": {key: "x"}})


def test_producer_info_has_no_execution_fields():
    info = ProducerInfo.create(
        producer_id=PRODUCER_ID,
        semantics_version=1,
        build_config=CONFIG,
        source=RECORDING,
    )
    payload = info.model_dump(mode="json")
    for extra in ("job_id", "pipeline_run_id", "generated_at"):
        with pytest.raises(ValidationError):
            ProducerInfo.model_validate({**payload, extra: "x"})


def test_producer_info_create_and_serialization_are_deterministic():
    first = ProducerInfo.create(
        producer_id=PRODUCER_ID,
        semantics_version=1,
        build_config=CONFIG,
        source=RECORDING,
    )
    second = ProducerInfo.create(
        producer_id=PRODUCER_ID,
        semantics_version=1,
        build_config=dict(reversed(list(CONFIG.items()))),
        source=RECORDING,
    )
    assert first == second
    first_bytes = canonical_json_bytes(first.model_dump(mode="json"))
    assert first_bytes == canonical_json_bytes(second.model_dump(mode="json"))
    assert first.producer_fingerprint == _fingerprint()
    assert ProducerInfo.model_validate_json(first_bytes) == first


def test_producer_info_verify_detects_a_mismatched_source():
    info = ProducerInfo.create(
        producer_id=PRODUCER_ID,
        semantics_version=1,
        build_config=CONFIG,
        source=RECORDING,
    )
    info.verify(RECORDING)
    with pytest.raises(ProducerFingerprintMismatchError):
        info.verify(EXTERNAL)
    tampered = ProducerInfo.model_validate(
        {**info.model_dump(mode="json"), "build_config": {"selected_channels": []}}
    )
    with pytest.raises(ProducerFingerprintMismatchError):
        tampered.verify(RECORDING)


@pytest.mark.parametrize(
    "overrides",
    [
        {"producer_id": "SceneOps.Builder"},
        {"producer_id": ""},
        {"semantics_version": 0},
        {"semantics_version": "1"},
        {"producer_fingerprint": "abc"},
    ],
)
def test_producer_info_rejects_invalid_fields(overrides):
    info = ProducerInfo.create(
        producer_id=PRODUCER_ID,
        semantics_version=1,
        build_config=CONFIG,
        source=RECORDING,
    )
    with pytest.raises(ValidationError):
        ProducerInfo.model_validate({**info.model_dump(mode="json"), **overrides})


class _Policy(StrEnum):
    FIXED = "fixed_window"


def test_normalize_build_config_produces_plain_json():
    assert normalize_build_config(
        {"policy": _Policy.FIXED, "channels": ("/a", "/b"), "threshold": 0.1}
    ) == {"channels": ["/a", "/b"], "policy": "fixed_window", "threshold": 0.1}


@pytest.mark.parametrize(
    "config",
    [
        {"channels": {"/a", "/b"}},
        {"threshold": float("nan")},
        {"threshold": float("inf")},
        {1: "non-string key"},
        {"path": object()},
        ["not", "an", "object"],
    ],
)
def test_normalize_build_config_rejects_non_canonical_values(config):
    with pytest.raises(ValueError):
        normalize_build_config(config)
