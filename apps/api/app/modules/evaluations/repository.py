from typing import Protocol

from sceneops_core.schemas.evaluations import (
    DetectionEvaluationRunManifest,
    DetectionSampleEvaluation,
)


class EvaluationRunRepository(Protocol):
    def list_evaluations(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        status: str | None = None,
    ) -> list[DetectionEvaluationRunManifest]: ...

    def get_evaluation(
        self,
        evaluation_run_id: str,
    ) -> DetectionEvaluationRunManifest | None: ...

    def list_sample_evaluations(
        self,
        evaluation_run_id: str,
    ) -> list[DetectionSampleEvaluation]: ...

    def get_sample_evaluation(
        self,
        evaluation_run_id: str,
        sample_id: str,
    ) -> DetectionSampleEvaluation | None: ...
