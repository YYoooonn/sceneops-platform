from __future__ import annotations

from sceneops_core.datasets.schemas import (
    DatasetValidationDecision,
    DatasetValidationReport,
    DatasetValidationSeverity,
    DatasetValidationStatus,
)


def decide_dataset_validation(
    report: DatasetValidationReport,
) -> DatasetValidationDecision:
    error_count = sum(
        1
        for issue in report.issues
        if issue.severity == DatasetValidationSeverity.ERROR
    )
    warning_count = sum(
        1
        for issue in report.issues
        if issue.severity == DatasetValidationSeverity.WARNING
    )

    if error_count > 0:
        return DatasetValidationDecision(
            status=DatasetValidationStatus.FAILED,
            should_block_pipeline=True,
            reason=f"Dataset validation has {error_count} blocking errors.",
        )

    if warning_count > 0:
        return DatasetValidationDecision(
            status=DatasetValidationStatus.WARNING,
            should_block_pipeline=False,
            reason=f"Dataset validation has {warning_count} warnings.",
        )

    return DatasetValidationDecision(
        status=DatasetValidationStatus.READY,
        should_block_pipeline=False,
        reason="Dataset validation passed.",
    )
