from __future__ import annotations

from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeRecord

from .reports import EpisodeValidationIssue, EpisodeValidationResult

# Identity fields that must agree between the persisted EpisodeRecord and the
# EpisodeManifest it claims to represent. (record_attr, manifest_value_getter)
_IDENTITY_CHECKS: tuple[tuple[str, str], ...] = (
    ("episode_id", "episode_id"),
    ("dataset_id", "dataset_id"),
    ("dataset_version", "dataset_version"),
)
_LINEAGE_IDENTITY_CHECKS: tuple[str, ...] = (
    "robot_id",
    "robot_run_id",
    "mission_id",
)


class EpisodeManifestValidator:
    """Structural usability checks for one Episode (SceneOps V2 Request 17).

    Answers "can this Episode safely be consumed by downstream processing?"
    using only facts already on EpisodeRecord/EpisodeManifest — no temporal
    alignment/synchronization (see docstring on start/end checks below,
    deferred to a future Temporal Semantics request).
    """

    def validate(
        self,
        *,
        record: EpisodeRecord,
        manifest: EpisodeManifest | None,
    ) -> EpisodeValidationResult:
        issues: list[EpisodeValidationIssue] = []
        checked_fields: list[str] = ["manifest_readable"]

        if manifest is None:
            issues.append(
                EpisodeValidationIssue(
                    type="manifest_not_found",
                    message=(
                        f"Episode manifest not found or unreadable: "
                        f"{record.episode_manifest_uri!r}"
                    ),
                    blocking=True,
                )
            )
            return EpisodeValidationResult(
                episode_id=record.episode_id,
                status="failed",
                should_block=True,
                checked_fields=checked_fields,
                issues=issues,
            )

        checked_fields.extend(
            [
                "frame_count",
                "observation_channels",
                "action_channels",
                "identity",
                "lineage",
                "robot_run_reference",
                "timestamps",
            ]
        )

        if manifest.frame_count == 0:
            issues.append(
                EpisodeValidationIssue(
                    type="empty_episode",
                    message="Episode has no observation or action frames",
                    blocking=True,
                )
            )

        if not manifest.observation_channels:
            issues.append(
                EpisodeValidationIssue(
                    type="no_observation_channels",
                    message="Episode has no observation channels",
                    blocking=True,
                )
            )

        if not manifest.action_channels:
            issues.append(
                EpisodeValidationIssue(
                    type="no_action_channels",
                    message=(
                        "Episode has no action channels — valid for "
                        "observation-only recordings, but worth flagging"
                    ),
                    blocking=False,
                    field="action_channels",
                )
            )

        for record_field, manifest_field in _IDENTITY_CHECKS:
            record_value = getattr(record, record_field)
            manifest_value = getattr(manifest, manifest_field)
            if record_value != manifest_value:
                issues.append(
                    EpisodeValidationIssue(
                        type="identity_mismatch",
                        message=(
                            f"EpisodeRecord.{record_field}={record_value!r} does not "
                            f"match EpisodeManifest.{manifest_field}={manifest_value!r}"
                        ),
                        blocking=True,
                        field=record_field,
                    )
                )

        for field_name in _LINEAGE_IDENTITY_CHECKS:
            record_value = getattr(record, field_name)
            manifest_value = getattr(manifest.lineage, field_name)
            if record_value != manifest_value:
                issues.append(
                    EpisodeValidationIssue(
                        type="identity_mismatch",
                        message=(
                            f"EpisodeRecord.{field_name}={record_value!r} does not "
                            f"match EpisodeManifest.lineage.{field_name}="
                            f"{manifest_value!r}"
                        ),
                        blocking=True,
                        field=field_name,
                    )
                )

        if record.robot_run_id is None:
            issues.append(
                EpisodeValidationIssue(
                    type="missing_robot_run_reference",
                    message=(
                        "Episode has no robot_run_id — valid for episodes built "
                        "directly from an mcap_uri, but worth flagging"
                    ),
                    blocking=False,
                    field="robot_run_id",
                )
            )

        if not manifest.lineage.raw_log_id:
            issues.append(
                EpisodeValidationIssue(
                    type="missing_raw_log_lineage",
                    message="Episode manifest lineage has no raw_log_id",
                    blocking=False,
                    field="raw_log_id",
                )
            )

        # Structural timestamp checks only — start <= end, no impossible
        # negative duration. NOT temporal alignment/synchronization (that's
        # observation/action clock-domain work, deferred to a future
        # Temporal Semantics request — see SceneOps V2 Request 17 §11).
        if (
            record.started_at is not None
            and record.ended_at is not None
            and record.started_at > record.ended_at
        ):
            issues.append(
                EpisodeValidationIssue(
                    type="invalid_time_range",
                    message=(
                        f"EpisodeRecord.started_at ({record.started_at}) is after "
                        f"ended_at ({record.ended_at})"
                    ),
                    blocking=True,
                    field="started_at",
                )
            )

        if (
            manifest.start_timestamp_us is not None
            and manifest.end_timestamp_us is not None
            and manifest.start_timestamp_us > manifest.end_timestamp_us
        ):
            issues.append(
                EpisodeValidationIssue(
                    type="invalid_time_range",
                    message=(
                        f"EpisodeManifest.start_timestamp_us "
                        f"({manifest.start_timestamp_us}) is after "
                        f"end_timestamp_us ({manifest.end_timestamp_us})"
                    ),
                    blocking=True,
                    field="start_timestamp_us",
                )
            )

        should_block = any(issue.blocking for issue in issues)
        status = "failed" if should_block else ("warning" if issues else "ready")

        return EpisodeValidationResult(
            episode_id=record.episode_id,
            status=status,
            should_block=should_block,
            checked_fields=checked_fields,
            frame_count=manifest.frame_count,
            observation_channels=list(manifest.observation_channels),
            action_channels=list(manifest.action_channels),
            issues=issues,
        )
