"""Quality checks over one canonical SceneManifest revision.

Structural integrity (ordering, references, source window, canonical form)
is already guaranteed by the SceneManifest contract; this validator reports
fitness for downstream use. Issues are aggregated per channel, because a
Scene keeps every observation of a channel and per-observation issues would
scale with sensor rate rather than with the problem.
"""

from __future__ import annotations

from collections import Counter

from sceneops_core.scenes.schemas import (
    SceneFrameRole,
    SceneManifest,
    SceneModality,
)

from .reports import SceneValidationIssue, SceneValidationResult


class SceneManifestValidator:
    def validate(
        self,
        *,
        scene_id: str,
        manifest: SceneManifest,
        required_channels: list[str] | None = None,
        validate_keyframes: bool = False,
        block_on_keyframe_missing_channels: bool = False,
    ) -> SceneValidationResult:
        required = list(required_channels or [])
        observed = manifest.observed_channel_names()
        observed_set = set(observed)
        keyframes = manifest.keyframes()
        issues: list[SceneValidationIssue] = []

        missing_channels = [ch for ch in required if ch not in observed_set]
        for channel in missing_channels:
            issues.append(
                SceneValidationIssue(
                    type="missing_channel",
                    message=f"Required channel has no observations: {channel}",
                    channel=channel,
                    blocking=True,
                )
            )

        for channel in manifest.channels:
            if channel.channel not in observed_set and channel.channel not in required:
                issues.append(
                    SceneValidationIssue(
                        type="empty_channel",
                        message=(
                            f"Channel {channel.channel} is part of the Scene but has "
                            "no observations inside its boundary"
                        ),
                        channel=channel.channel,
                    )
                )

        if validate_keyframes and required and keyframes:
            observation_channel = {
                o.observation_id: o.channel for o in manifest.observations
            }
            lacking: Counter[str] = Counter()
            for group in keyframes:
                present = {observation_channel[i] for i in group.observation_ids}
                lacking.update(ch for ch in required if ch not in present)
            for channel, count in sorted(lacking.items()):
                issues.append(
                    SceneValidationIssue(
                        type="keyframe_missing_channel",
                        message=(
                            f"{count} of {len(keyframes)} keyframes have no "
                            f"observation of {channel}"
                        ),
                        channel=channel,
                        count=count,
                        blocking=block_on_keyframe_missing_channels,
                    )
                )

        issues.extend(_geometry_issues(manifest))

        should_block = any(issue.blocking for issue in issues)
        return SceneValidationResult(
            scene_id=scene_id,
            status="failed" if should_block else ("warning" if issues else "ready"),
            should_block=should_block,
            required_channels=required,
            observed_channels=observed,
            missing_channels=missing_channels,
            observation_count=len(manifest.observations),
            keyframe_count=len(keyframes),
            issues=issues,
        )


def _geometry_issues(manifest: SceneManifest) -> list[SceneValidationIssue]:
    modality = {c.channel: c.modality for c in manifest.channels}
    calibrations = {c.calibration_id: c for c in manifest.calibrations}

    without_calibration: Counter[str] = Counter()
    without_intrinsic: Counter[str] = Counter()
    without_image_size: Counter[str] = Counter()
    for observation in manifest.observations:
        channel = observation.channel
        calibration = calibrations.get(observation.calibration_id or "")
        if calibration is None:
            without_calibration[channel] += 1
        if modality[channel] == SceneModality.CAMERA:
            if calibration is not None and calibration.camera_intrinsic is None:
                without_intrinsic[channel] += 1
            if observation.image_size is None:
                without_image_size[channel] += 1

    issues: list[SceneValidationIssue] = []
    for issue_type, counts, what in (
        ("missing_calibration", without_calibration, "have no calibration"),
        ("missing_camera_intrinsic", without_intrinsic, "have no camera intrinsic"),
        ("missing_image_size", without_image_size, "have no image size"),
    ):
        for channel, count in sorted(counts.items()):
            issues.append(
                SceneValidationIssue(
                    type=issue_type,
                    message=f"{count} observation(s) of {channel} {what}",
                    channel=channel,
                    count=count,
                )
            )

    ego_frames = {
        f.frame_id for f in manifest.coordinate_frames if f.role == SceneFrameRole.EGO
    }
    if not any(p.transform.child_frame_id in ego_frames for p in manifest.poses):
        issues.append(
            SceneValidationIssue(
                type="missing_ego_poses",
                message="Scene carries no source ego poses",
            )
        )
    return issues
