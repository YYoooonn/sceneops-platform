from __future__ import annotations

from sceneops_core.datasets.schemas import DatasetValidationSeverity
from sceneops_core.datasets.schemas import (
    DatasetValidationReport,
    DatasetValidationScope,
    DatasetValidationStatus,
    DatasetValidationSummary,
)
from sceneops_worker.datasets.validation.base import (
    DatasetValidationRequest,
    DatasetValidationResult,
    DatasetValidator,
)
from sceneops_worker.datasets.validation.checks import (
    missing_required_channel_issue,
    missing_sample_manifest_issue,
    missing_scene_manifest_issue,
)
from sceneops_worker.datasets.validation.policy import decide_dataset_validation


class ManifestDatasetValidator(DatasetValidator):
    @property
    def validator_id(self) -> str:
        return "manifest-validator"

    async def run(
        self,
        request: DatasetValidationRequest,
    ) -> DatasetValidationResult:
        return await _validate_dataset_manifest(request)


async def _validate_dataset_manifest(
    request: DatasetValidationRequest,
) -> DatasetValidationReport:
    dataset_manifest = request.dataset_manifest
    dataset_artifact_store = request.dataset_artifact_store

    scope = (
        DatasetValidationScope.SAMPLED
        if request.max_samples is not None
        else DatasetValidationScope.FULL
    )

    summary = DatasetValidationSummary(
        scene_count=dataset_manifest.summary.scene_count,
        sample_count=dataset_manifest.summary.sample_count,
        annotation_count=dataset_manifest.summary.annotation_count,
    )

    issues = []
    scene_index = await dataset_artifact_store.load_scene_index(
        dataset_manifest.uris.scene_index
    )

    checked_samples = 0

    for scene_item in scene_index.scenes:
        scene_manifest = await dataset_artifact_store.load_scene_manifest(
            scene_item.manifest_uri
        )

        if scene_manifest is None:
            issues.append(
                missing_scene_manifest_issue(
                    scene_id=scene_item.scene_id,
                    uri=scene_item.manifest_uri,
                )
            )
            continue

        summary.validated_scene_count += 1

        if not request.validate_samples:
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
                issues.append(
                    missing_sample_manifest_issue(
                        sample_id=sample_id,
                        uri=sample_uri,
                    )
                )
                continue

            summary.validated_sample_count += 1
            sample_sensors = getattr(sample_manifest, "sensors", {}) or {}

            for channel in request.require_target_channels:
                if channel not in sample_sensors:
                    issues.append(
                        missing_required_channel_issue(
                            sample_id=sample_id,
                            channel=channel,
                        )
                    )

            checked_samples += 1

            if (
                request.max_samples is not None
                and checked_samples >= request.max_samples
            ):
                break

        if request.max_samples is not None and checked_samples >= request.max_samples:
            break

    summary.issue_count = len(issues)
    summary.error_count = sum(
        1 for issue in issues if issue.severity == DatasetValidationSeverity.ERROR
    )
    summary.warning_count = sum(
        1 for issue in issues if issue.severity == DatasetValidationSeverity.WARNING
    )
    summary.missing_scene_count = sum(
        1 for issue in issues if issue.code == "missing_scene_manifest"
    )
    summary.missing_sample_count = sum(
        1 for issue in issues if issue.code == "missing_sample_manifest"
    )
    summary.missing_channel_count = sum(
        1 for issue in issues if issue.code == "missing_required_channel"
    )

    report = DatasetValidationReport(
        validation_run_id=request.validation_run_id,
        job_id=request.job_id,
        dataset_id=request.dataset_id,
        dataset_version=request.dataset_version,
        dataset_manifest_uri=request.dataset_manifest_uri,
        status=DatasetValidationStatus.READY,
        scope=scope,
        max_samples=request.max_samples,
        should_block_pipeline=False,
        summary=summary,
        issues=issues,
        metadata={
            "validator_id": "manifest-validator",
            "validate_samples": request.validate_samples,
            "validate_sensor_artifacts": request.validate_sensor_artifacts,
            "validate_annotations": request.validate_annotations,
            "validate_calibration": request.validate_calibration,
            "required_channels": request.require_target_channels,
        },
    )

    decision = decide_dataset_validation(report)
    report.status = decision.status
    report.should_block_pipeline = decision.should_block_pipeline
    report.metadata["decision_reason"] = decision.reason

    return report
