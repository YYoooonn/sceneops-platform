"""Canonical EpisodeManifest v1 / EpisodeRecord contract (ADR-007 §31)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.episodes import (
    EpisodeReadiness,
    derive_episode_readiness,
    latest_run_for_revision,
)
from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeManifestError,
    EpisodeValidationRunRecord,
    NonCanonicalEpisodeManifestError,
    episode_id_for,
    load_canonical_episode_manifest,
    project_episode_record,
)
from sceneops_core.episodes.testing import (
    action,
    episode_manifest,
    event,
    observation,
    state,
)
from sceneops_core.runs.schemas import RunStatus


def _asynchronous() -> EpisodeManifest:
    """Observations at t0, t3, t8; states at t1, t2, t7; actions at t2, t5,
    t9 (seconds), never put on a common timeline."""
    s = 1_000_000_000
    return episode_manifest(
        [
            observation("/cam", 0 * s),
            observation("/cam", 3 * s),
            observation("/cam", 8 * s),
            state("/odom", 1 * s, x=1.0, moving=True),
            state("/odom", 2 * s, x=2.0, moving=True),
            state("/odom", 7 * s, x=7.0, moving=False),
            action("/control", 2 * s, steering=0.1, gear=3),
            action("/control", 5 * s, steering=0.2, gear=3),
            action("/control", 9 * s, steering=-0.1, gear=4),
            event("/mission", 0, mission_id="m-1", state="running"),
            event("/mission", 9 * s, mission_id="m-1", state="completed"),
        ]
    )


class TestManifest:
    def test_asynchronous_streams_are_kept_as_recorded(self) -> None:
        manifest = _asynchronous()
        assert [o.timestamp_ns // 10**9 for o in manifest.observations] == [0, 3, 8]
        assert [o.timestamp_ns // 10**9 for o in manifest.states] == [1, 2, 7]
        assert [o.timestamp_ns // 10**9 for o in manifest.actions] == [2, 5, 9]
        # values keep their source types: no float coercion of int / bool
        assert manifest.actions[0].values == {"gear": 3, "steering": 0.1}
        assert manifest.states[0].values["moving"] is True

    def test_canonical_bytes_round_trip(self) -> None:
        manifest = _asynchronous()
        data = manifest.to_canonical_bytes()
        loaded = load_canonical_episode_manifest(data)
        assert loaded == manifest
        assert loaded.to_canonical_bytes() == data
        assert isinstance(json.loads(data)["actions"][0]["values"]["gear"], int)

    def test_non_canonical_bytes_are_rejected(self) -> None:
        data = json.dumps(json.loads(_asynchronous().to_canonical_bytes()), indent=2)
        with pytest.raises(NonCanonicalEpisodeManifestError):
            load_canonical_episode_manifest(data.encode())

    def test_unknown_schema_version_is_rejected(self) -> None:
        payload = json.loads(_asynchronous().to_canonical_bytes())
        payload["schema_version"] = "sceneops.episode_manifest/v0"
        with pytest.raises(EpisodeManifestError):
            load_canonical_episode_manifest(json.dumps(payload).encode())

    def test_duplicate_source_events_stay_separate_occurrences(self) -> None:
        manifest = episode_manifest(
            [state("/odom", 5, x=1.0), state("/odom", 5, x=1.0)]
        )
        assert [o.occurrence_id for o in manifest.states] == [
            "odom-00000000",
            "odom-00000001",
        ]

    def test_window_is_half_open_in_its_own_clock(self) -> None:
        episode_manifest([state("/odom", 9, x=1.0)], window=(0, 10))
        with pytest.raises(ValidationError, match="outside the episode window"):
            episode_manifest([state("/odom", 10, x=1.0)], window=(0, 10))

    def test_timestamps_in_other_clocks_are_never_compared_with_the_window(
        self,
    ) -> None:
        manifest = episode_manifest(
            [
                state("/odom", 5, x=1.0),
                action("/control", 10**18, clock="robot.control_clock", u=1.0),
            ],
            window=(0, 10),
        )
        assert manifest.actions[0].timestamp_ns == 10**18

    def test_occurrence_must_match_its_stream_role_and_fields(self) -> None:
        payload = json.loads(_asynchronous().to_canonical_bytes())
        payload["states"], payload["actions"] = payload["actions"], payload["states"]
        with pytest.raises(ValidationError):
            EpisodeManifest.model_validate(payload)
        payload = json.loads(_asynchronous().to_canonical_bytes())
        payload["actions"][0]["values"]["extra"] = 1
        with pytest.raises(ValidationError, match="its stream selects"):
            EpisodeManifest.model_validate(payload)

    def test_non_finite_values_are_not_representable(self) -> None:
        with pytest.raises(ValidationError):
            episode_manifest([state("/odom", 1, x=float("nan"))])

    def test_fingerprint_must_re_derive_from_the_source(self) -> None:
        payload = json.loads(_asynchronous().to_canonical_bytes())
        payload["lineage"]["source"]["recording_checksum"] = "sha256:" + "2" * 64
        with pytest.raises(ValidationError, match="producer_fingerprint"):
            EpisodeManifest.model_validate(payload)

    def test_an_episode_holds_at_least_one_occurrence(self) -> None:
        payload = json.loads(_asynchronous().to_canonical_bytes())
        for key in ("observations", "states", "actions", "events"):
            payload[key] = []
        with pytest.raises(ValidationError, match="at least one"):
            EpisodeManifest.model_validate(payload)

    def test_manifest_carries_no_label_or_alignment_fields(self) -> None:
        fields = set(EpisodeManifest.model_fields)
        assert not fields & {
            "task",
            "outcome",
            "control_frequency_hz",
            "frame_count",
            "episode_id",
            "dataset_id",
        }


class TestRecordAndIdentity:
    def test_record_projects_the_manifest(self) -> None:
        manifest = _asynchronous()
        record = project_episode_record(
            dataset_id="ds",
            dataset_version="v1",
            manifest=manifest,
            manifest_artifact_id="episode-manifest-x",
            manifest_checksum=manifest.checksum(),
        )
        assert record.episode_id == episode_id_for(
            dataset_id="ds", dataset_version="v1", source=manifest.lineage.source
        )
        assert record.robot_run_id == "run-001"
        assert record.unit_key == "recording"
        assert (record.observation_count, record.state_count) == (3, 3)
        assert (record.action_count, record.event_count) == (3, 2)
        assert record.action_topics == ["/control"]
        assert record.window_clock == "sensor.header_stamp"

    def test_episode_id_is_dataset_version_scoped_and_domain_distinct(self) -> None:
        source = _asynchronous().lineage.source
        a = episode_id_for(dataset_id="ds", dataset_version="v1", source=source)
        b = episode_id_for(dataset_id="ds", dataset_version="v2", source=source)
        assert a != b and a.startswith("episode-")


class TestRunPinsAndReadiness:
    def test_per_episode_run_must_pin_a_revision(self) -> None:
        with pytest.raises(ValidationError):
            EpisodeValidationRunRecord(
                run_id="r", status=RunStatus.SUCCEEDED, episode_id="e"
            )
        with pytest.raises(ValidationError):
            EpisodeValidationRunRecord(
                run_id="r",
                status=RunStatus.SUCCEEDED,
                manifest_artifact_id="a",
                manifest_checksum="c",
            )

    def test_readiness_counts_only_the_current_revision(self) -> None:
        old = EpisodeValidationRunRecord(
            run_id="old",
            status=RunStatus.SUCCEEDED,
            episode_id="e",
            manifest_artifact_id="a1",
            manifest_checksum="c1",
            validation_status="ready",
        )
        assert (
            latest_run_for_revision(
                [old], episode_id="e", manifest_artifact_id="a2", manifest_checksum="c2"
            )
            is None
        )
        current = latest_run_for_revision(
            [old], episode_id="e", manifest_artifact_id="a1", manifest_checksum="c1"
        )
        assert derive_episode_readiness(current) == EpisodeReadiness.READY
        assert derive_episode_readiness(None) == EpisodeReadiness.UNKNOWN


def test_roles_cover_observation_state_action_event() -> None:
    assert {r.value for r in EpisodeStreamRole} == {
        "observation",
        "state",
        "action",
        "event",
    }
