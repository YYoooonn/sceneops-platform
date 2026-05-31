from __future__ import annotations

from sceneops_core.schemas.datasets.validation import (
    DatasetValidationCheckType,
    DatasetValidationIssue,
    DatasetValidationSeverity,
)


def missing_scene_manifest_issue(
    *,
    scene_id: str,
    uri: str,
) -> DatasetValidationIssue:
    return DatasetValidationIssue(
        check_type=DatasetValidationCheckType.SCENE_MANIFEST,
        severity=DatasetValidationSeverity.ERROR,
        code="missing_scene_manifest",
        message=f"Scene manifest not found: {scene_id}",
        scene_id=scene_id,
        uri=uri,
    )


def missing_sample_manifest_issue(
    *,
    sample_id: str,
    uri: str,
) -> DatasetValidationIssue:
    return DatasetValidationIssue(
        check_type=DatasetValidationCheckType.SAMPLE_MANIFEST,
        severity=DatasetValidationSeverity.ERROR,
        code="missing_sample_manifest",
        message=f"Sample manifest not found: {sample_id}",
        sample_id=sample_id,
        uri=uri,
    )


def missing_required_channel_issue(
    *,
    sample_id: str,
    channel: str,
) -> DatasetValidationIssue:
    return DatasetValidationIssue(
        check_type=DatasetValidationCheckType.REQUIRED_SENSOR_CHANNELS,
        severity=DatasetValidationSeverity.ERROR,
        code="missing_required_channel",
        message=f"Required sensor channel is missing: {channel}",
        sample_id=sample_id,
        channel=channel,
    )
