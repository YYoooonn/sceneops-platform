from __future__ import annotations

from sceneops_core.jobs.schemas import JobType, ValidateDatasetJobResult
from sceneops_worker.pipelines.errors import PipelineBlockedByValidationError


class PipelineQualityGate:
    def check_step_result(
        self,
        *,
        job_type: JobType,
        result: dict | None,
    ) -> None:
        if result is None:
            return

        if job_type == JobType.VALIDATE_DATASET:
            self._check_dataset_validation(result)
            return

    def _check_dataset_validation(self, result: dict) -> None:
        parsed = ValidateDatasetJobResult.model_validate(result)

        if parsed.should_block_pipeline:
            raise PipelineBlockedByValidationError(
                "Dataset validation blocked pipeline: "
                f"dataset={parsed.dataset_id}:{parsed.dataset_version}, "
                f"status={parsed.status.value}, "
                f"report={parsed.validation_report_uri}"
            )
