"""Tests for EpisodeManifestValidator (SceneOps V2 Request 17).

Structural usability checks only — no temporal alignment/synchronization
(see the validator's own docstring). Covers: valid episode, missing
manifest, empty episode, missing observation/action channels, Record/
Manifest identity mismatches, missing RobotRun reference, invalid time
ranges.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sceneops_core.episodes.schemas import (
    EpisodeLineage,
    EpisodeManifest,
    EpisodeObservationFrame,
    EpisodeOutcome,
    EpisodeRecord,
    EpisodeStatus,
)
from sceneops_worker.episodes.validation import EpisodeManifestValidator


def _record(**overrides) -> EpisodeRecord:
    defaults = dict(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        robot_id="robot-1",
        robot_run_id="run-1",
        mission_id="m1",
        status=EpisodeStatus.REGISTERED,
        episode_manifest_uri="mem://episodes/ep-1.json",
        frame_count=2,
    )
    defaults.update(overrides)
    return EpisodeRecord(**defaults)


def _manifest(**overrides) -> EpisodeManifest:
    defaults = dict(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(
            raw_log_id="rl1", robot_id="robot-1", robot_run_id="run-1", mission_id="m1"
        ),
        outcome=EpisodeOutcome.SUCCESS,
        observation_frames=[
            EpisodeObservationFrame(timestamp_us=1_000_000, channel="state.position")
        ],
        observation_channels=["state.position"],
        action_channels=["steering"],
        start_timestamp_us=1_000_000,
        end_timestamp_us=2_000_000,
        frame_count=2,
    )
    defaults.update(overrides)
    return EpisodeManifest(**defaults)


class TestValidEpisode:
    def test_fully_consistent_episode_is_ready_with_no_issues(self) -> None:
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=_manifest()
        )
        assert result.status == "ready"
        assert result.should_block is False
        assert result.issues == []
        assert result.frame_count == 2

    def test_checked_fields_recorded(self) -> None:
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=_manifest()
        )
        assert "manifest_readable" in result.checked_fields
        assert "identity" in result.checked_fields
        assert "timestamps" in result.checked_fields


class TestMissingManifest:
    def test_missing_manifest_is_blocking(self) -> None:
        result = EpisodeManifestValidator().validate(record=_record(), manifest=None)
        assert result.status == "failed"
        assert result.should_block is True
        assert len(result.issues) == 1
        assert result.issues[0].type == "manifest_not_found"
        assert result.issues[0].blocking is True
        # Short-circuits — no other checks run once the manifest is unreadable.
        assert result.checked_fields == ["manifest_readable"]


class TestEmptyEpisode:
    def test_zero_frame_count_is_blocking(self) -> None:
        manifest = _manifest(
            frame_count=0, observation_frames=[], observation_channels=[]
        )
        result = EpisodeManifestValidator().validate(
            record=_record(frame_count=0), manifest=manifest
        )
        assert result.should_block is True
        types = {i.type for i in result.issues}
        assert "empty_episode" in types


class TestChannelChecks:
    def test_no_observation_channels_is_blocking(self) -> None:
        manifest = _manifest(observation_channels=[])
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        issue = next(i for i in result.issues if i.type == "no_observation_channels")
        assert issue.blocking is True
        assert result.should_block is True

    def test_no_action_channels_is_warning_not_blocking(self) -> None:
        manifest = _manifest(action_channels=[])
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        issue = next(i for i in result.issues if i.type == "no_action_channels")
        assert issue.blocking is False
        assert result.should_block is False
        assert result.status == "warning"


class TestIdentityMismatch:
    def test_episode_id_mismatch_is_blocking(self) -> None:
        manifest = _manifest(episode_id="different-episode-id")
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        issue = next(
            i
            for i in result.issues
            if i.type == "identity_mismatch" and i.field == "episode_id"
        )
        assert issue.blocking is True
        assert result.should_block is True

    def test_dataset_id_mismatch_is_blocking(self) -> None:
        manifest = _manifest(dataset_id="different-dataset")
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        assert result.should_block is True
        assert any(
            i.type == "identity_mismatch" and i.field == "dataset_id"
            for i in result.issues
        )

    def test_mission_id_lineage_mismatch_is_blocking(self) -> None:
        manifest = _manifest(
            lineage=EpisodeLineage(
                raw_log_id="rl1",
                robot_id="robot-1",
                robot_run_id="run-1",
                mission_id="different-mission",
            )
        )
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        assert result.should_block is True
        assert any(
            i.type == "identity_mismatch" and i.field == "mission_id"
            for i in result.issues
        )

    def test_matching_identity_produces_no_identity_issues(self) -> None:
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=_manifest()
        )
        assert not any(i.type == "identity_mismatch" for i in result.issues)


class TestRobotRunReference:
    def test_missing_robot_run_id_is_warning(self) -> None:
        result = EpisodeManifestValidator().validate(
            record=_record(robot_run_id=None),
            manifest=_manifest(
                lineage=EpisodeLineage(
                    raw_log_id="rl1", robot_id="robot-1", mission_id="m1"
                )
            ),
        )
        issue = next(
            i for i in result.issues if i.type == "missing_robot_run_reference"
        )
        assert issue.blocking is False
        assert result.should_block is False


class TestLineageCoherence:
    def test_missing_raw_log_id_is_warning(self) -> None:
        manifest = _manifest(
            lineage=EpisodeLineage(
                robot_id="robot-1", robot_run_id="run-1", mission_id="m1"
            )
        )
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        issue = next(i for i in result.issues if i.type == "missing_raw_log_lineage")
        assert issue.blocking is False


class TestTimestampConsistency:
    def test_record_started_after_ended_is_blocking(self) -> None:
        record = _record(
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        result = EpisodeManifestValidator().validate(
            record=record, manifest=_manifest()
        )
        assert result.should_block is True
        assert any(
            i.type == "invalid_time_range" and i.field == "started_at"
            for i in result.issues
        )

    def test_manifest_start_after_end_is_blocking(self) -> None:
        manifest = _manifest(start_timestamp_us=5_000_000, end_timestamp_us=1_000_000)
        result = EpisodeManifestValidator().validate(
            record=_record(), manifest=manifest
        )
        assert result.should_block is True
        assert any(
            i.type == "invalid_time_range" and i.field == "start_timestamp_us"
            for i in result.issues
        )

    def test_valid_ordered_timestamps_produce_no_issue(self) -> None:
        record = _record(
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        result = EpisodeManifestValidator().validate(
            record=record, manifest=_manifest()
        )
        assert not any(i.type == "invalid_time_range" for i in result.issues)

    def test_missing_timestamps_are_not_flagged(self) -> None:
        """Absence of timestamps is not itself an error — only inconsistency
        between a present start and end is checked (structural only, no
        alignment/synchronization judgment)."""
        result = EpisodeManifestValidator().validate(
            record=_record(started_at=None, ended_at=None), manifest=_manifest()
        )
        assert not any(i.type == "invalid_time_range" for i in result.issues)
