from __future__ import annotations

from sceneops_core.jobs.schemas import (
    BuildDatasetManifestJobResult,
    JobType,
    ValidateSceneJobResult,
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

        if job_type == JobType.VALIDATE_SCENE:
            self._check_scene_validation(result)
            return

        if job_type == JobType.BUILD_DATASET_MANIFEST:
            self._check_manifest_build(result)
            return

    def _check_scene_validation(self, result: dict) -> None:
        parsed = ValidateSceneJobResult.model_validate(result)

        if parsed.should_block_pipeline:
            raise PipelineBlockedByValidationError(
                "Scene validation blocked pipeline: "
                f"status={parsed.status}, "
                f"issues={parsed.issue_count}, "
                f"scenes_checked={parsed.checked_scene_count}"
            )

    def _check_manifest_build(self, result: dict) -> None:
        parsed = BuildDatasetManifestJobResult.model_validate(result)

        if parsed.should_block_pipeline:
            raise PipelineBlockedByValidationError(
                "Dataset manifest build blocked pipeline: "
                f"dataset={parsed.dataset_id}:{parsed.dataset_version}"
            )
