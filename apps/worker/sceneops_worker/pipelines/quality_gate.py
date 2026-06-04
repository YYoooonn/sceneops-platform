from __future__ import annotations

from sceneops_core.jobs.schemas import (
    BuildDatasetManifestJobResult,
    JobType,
)
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

        if job_type == JobType.BUILD_DATASET_MANIFEST:
            self._check_manifest_build(result)
            return

    def _check_manifest_build(self, result: dict) -> None:
        # TODO Phase 2B: update ValidateDatasetJobHandler to BuildDatasetManifestJobHandler.
        # Currently skipping the quality gate check since ValidateDatasetJobResult is gone.
        # Once the handler is replaced, use BuildDatasetManifestJobResult here.
        try:
            parsed = BuildDatasetManifestJobResult.model_validate(result)
            if parsed.should_block_pipeline:
                raise PipelineBlockedByValidationError(
                    "Dataset manifest build blocked pipeline: "
                    f"dataset={parsed.dataset_id}:{parsed.dataset_version}"
                )
        except Exception:
            pass
