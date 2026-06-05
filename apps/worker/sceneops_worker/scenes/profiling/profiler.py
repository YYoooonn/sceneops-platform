from __future__ import annotations

from collections import Counter

from sceneops_core.scenes.schemas.manifests import SceneManifest

from .reports import SceneProfileResult


class SceneManifestProfiler:
    def profile(self, *, manifest: SceneManifest) -> SceneProfileResult:
        annotation_count = sum(len(s.annotations) for s in manifest.samples)

        category_counts: Counter[str] = Counter()
        for sample in manifest.samples:
            for annotation in sample.annotations:
                category = getattr(annotation, "category", None) or "unknown"
                category_counts[str(category)] += 1

        return SceneProfileResult(
            scene_id=manifest.scene_id,
            sample_count=manifest.sample_count,
            frame_count=manifest.frame_count,
            annotation_count=annotation_count,
            channels=list(manifest.channels),
            category_distribution=dict(category_counts),
        )
