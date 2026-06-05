from __future__ import annotations

from sceneops_core.scenes.schemas.manifests import SceneManifest

from .reports import SceneValidationIssue, SceneValidationResult


class SceneManifestValidator:
    def validate(
        self,
        *,
        manifest: SceneManifest,
        required_channels: list[str] | None = None,
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
