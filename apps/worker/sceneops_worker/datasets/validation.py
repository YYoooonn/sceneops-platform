from __future__ import annotations

from dataclasses import dataclass, field

from sceneops_core.schemas.datasets import DatasetManifest
from sceneops_worker.datasets import DatasetArtifactStore


@dataclass
class DatasetValidationReport:
    scene_count: int
    sample_count: int
    annotation_count: int
    validated_scene_count: int = 0
    validated_sample_count: int = 0
    missing_sample_ids: list[str] = field(default_factory=list)
    missing_scene_ids: list[str] = field(default_factory=list)
    missing_channels: dict[str, list[str]] = field(default_factory=dict)

    @property
    def missing_sample_count(self) -> int:
        return len(self.missing_sample_ids)

    @property
    def is_valid(self) -> bool:
        return (
            not self.missing_sample_ids
            and not self.missing_scene_ids
            and not self.missing_channels
        )


async def validate_dataset_manifest(
    *,
    dataset_manifest: DatasetManifest,
    dataset_artifact_store: DatasetArtifactStore,
    require_target_channels: list[str],
    validate_samples: bool = True,
    max_samples: int | None = None,
) -> DatasetValidationReport:
    scene_index = await dataset_artifact_store.load_scene_index(
        dataset_manifest.uris.scene_index
    )

    if scene_index is None:
        raise FileNotFoundError(
            f"Scene index not found: {dataset_manifest.uris.scene_index}"
        )

    report = DatasetValidationReport(
        scene_count=dataset_manifest.summary.scene_count,
        sample_count=dataset_manifest.summary.sample_count,
        annotation_count=dataset_manifest.summary.annotation_count,
    )

    checked_samples = 0

    for scene_item in scene_index.scenes:
        scene_manifest = await dataset_artifact_store.load_scene_manifest(
            scene_item.manifest_uri
        )

        if scene_manifest is None:
            report.missing_scene_ids.append(scene_item.scene_id)
            continue

        report.validated_scene_count += 1

        if not validate_samples:
            continue

        for sample_id in scene_manifest.sample_ids:
            sample_uri = dataset_artifact_store.sample_manifest_uri(
                version_root_uri=dataset_manifest.uris.manifest_root,
                sample_id=sample_id,
            )

            sample_manifest = await dataset_artifact_store.load_sample_manifest(
                sample_uri
            )

            if sample_manifest is None:
                report.missing_sample_ids.append(sample_id)
                continue

            report.validated_sample_count += 1

            missing_channels = [
                channel
                for channel in require_target_channels
                if channel not in sample_manifest.sensors
            ]

            if missing_channels:
                report.missing_channels[sample_id] = missing_channels

            checked_samples += 1
            if max_samples is not None and checked_samples >= max_samples:
                return report

    return report
