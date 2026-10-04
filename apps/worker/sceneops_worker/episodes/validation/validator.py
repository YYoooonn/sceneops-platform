from __future__ import annotations

from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeRecord,
    project_episode_record,
)

from .reports import EpisodeValidationIssue, EpisodeValidationResult

_PROJECTED_FIELDS = (
    "episode_id",
    "robot_run_id",
    "unit_key",
    "producer_fingerprint",
    "window_clock",
    "window_start_timestamp_ns",
    "window_end_timestamp_ns",
    "observation_topics",
    "state_topics",
    "action_topics",
    "event_topics",
    "observation_count",
    "state_count",
    "action_count",
    "event_count",
)


class EpisodeManifestValidator:
    """Structural usability checks of one registered Episode revision.

    Answers "can this Episode safely be consumed downstream?" from the
    record and the manifest revision it pins. It never aligns or compares
    timestamps across clocks: asynchronous streams are the canonical form,
    not an issue. The manifest's own invariants (canonical order, window,
    stream roles, fingerprint) already held when it was parsed.
    """

    def validate(
        self, *, record: EpisodeRecord, manifest: EpisodeManifest
    ) -> EpisodeValidationResult:
        issues: list[EpisodeValidationIssue] = []
        checked = ["record_projection", "stream_presence", "action_streams"]

        expected = project_episode_record(
            dataset_id=record.dataset_id,
            dataset_version=record.dataset_version,
            manifest=manifest,
            manifest_artifact_id=record.manifest_artifact_id,
            manifest_checksum=record.manifest_checksum,
        )
        for name in _PROJECTED_FIELDS:
            if getattr(record, name) != getattr(expected, name):
                issues.append(
                    EpisodeValidationIssue(
                        type="projection_mismatch",
                        message=(
                            f"EpisodeRecord.{name}={getattr(record, name)!r} is not the "
                            f"manifest's {getattr(expected, name)!r}"
                        ),
                        blocking=True,
                        field=name,
                    )
                )

        observed = {o.topic for o in manifest.occurrences()}
        for stream in manifest.streams:
            if stream.topic not in observed:
                issues.append(
                    EpisodeValidationIssue(
                        type="stream_absent",
                        message=(
                            f"{stream.role.value} stream {stream.topic!r} has no "
                            "occurrence in the episode window"
                        ),
                        field=stream.topic,
                    )
                )
        if not any(s.role == EpisodeStreamRole.ACTION for s in manifest.streams):
            issues.append(
                EpisodeValidationIssue(
                    type="no_action_streams",
                    message="the Episode declares no action stream (observation-only)",
                )
            )
        if not (manifest.observations or manifest.states or manifest.actions):
            issues.append(
                EpisodeValidationIssue(
                    type="no_stream_occurrences",
                    message="the Episode holds only event markers",
                )
            )

        should_block = any(i.blocking for i in issues)
        return EpisodeValidationResult(
            episode_id=record.episode_id,
            status="failed" if should_block else ("warning" if issues else "ready"),
            should_block=should_block,
            checked_fields=checked,
            observation_count=len(manifest.observations),
            state_count=len(manifest.states),
            action_count=len(manifest.actions),
            event_count=len(manifest.events),
            issues=issues,
        )
