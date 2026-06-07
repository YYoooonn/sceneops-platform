from __future__ import annotations

from sceneops_core.scenes.schemas.manifests import SceneManifest

from .reports import SceneValidationIssue, SceneValidationResult


class SceneManifestValidator:
    def validate(
        self,
        *,
        manifest: SceneManifest,
        required_channels: list[str] | None = None,
        validate_samples: bool = False,
        block_on_sample_missing_channels: bool = False,
    ) -> SceneValidationResult:
        required = required_channels or []
        observed = manifest.channels
        observed_set = set(observed)

        issues: list[SceneValidationIssue] = []

        if manifest.sample_count == 0:
            issues.append(
                SceneValidationIssue(
                    type="empty_scene",
                    message="Scene has no samples",
                    blocking=True,
                )
            )

        # Scene-level channel check — always blocking
        missing_channels = [ch for ch in required if ch not in observed_set]
        for ch in missing_channels:
            issues.append(
                SceneValidationIssue(
                    type="missing_channel",
                    message=f"Required channel missing: {ch}",
                    channel=ch,
                    blocking=True,
                )
            )

        # Sample-level channel check — blocking only if explicitly requested
        if validate_samples and required:
            for sample in manifest.samples:
                sample_channels = {sf.channel for sf in sample.sensor_frames}
                for ch in required:
                    if ch not in sample_channels:
                        issues.append(
                            SceneValidationIssue(
                                type="sample_missing_channel",
                                message=f"Sample {sample.sample_id} missing channel: {ch}",
                                channel=ch,
                                blocking=block_on_sample_missing_channels,
                            )
                        )

        should_block = any(i.blocking for i in issues)

        return SceneValidationResult(
            scene_id=manifest.scene_id,
            status="failed" if should_block else "ready",
            should_block=should_block,
            required_channels=required,
            observed_channels=list(observed),
            missing_channels=missing_channels,
            sample_count=manifest.sample_count,
            frame_count=manifest.frame_count,
            issues=issues,
        )
