import json
from pathlib import Path
from typing import Any

from sceneops_core.paths.runs import (
    evaluation_run_manifest_path,
    evaluation_run_root,
    sample_evaluation_manifest_path,
)
from sceneops_core.schemas.evaluations import (
    DetectionEvaluationRunManifest,
    DetectionSampleEvaluation,
)


class LocalEvaluationRunRepository:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root

    def list_evaluations(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        status: str | None = None,
    ) -> list[DetectionEvaluationRunManifest]:
        evaluations_root = self.runs_root / "evaluations"

        if not evaluations_root.exists():
            return []

        evaluations: list[DetectionEvaluationRunManifest] = []

        for evaluation_dir in sorted(evaluations_root.iterdir()):
            if not evaluation_dir.is_dir():
                continue

            evaluation = self.get_evaluation(evaluation_dir.name)
            if evaluation is None:
                continue

            if not _matches_filters(
                evaluation=evaluation,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                model_id=model_id,
                model_version=model_version,
                inference_run_id=inference_run_id,
                status=status,
            ):
                continue

            evaluations.append(evaluation)

        return evaluations

    def get_evaluation(
        self,
        evaluation_run_id: str,
    ) -> DetectionEvaluationRunManifest | None:
        path = evaluation_run_manifest_path(
            runs_root=self.runs_root,
            evaluation_run_id=evaluation_run_id,
        )

        data = self._read_json_or_none(path)
        if data is None:
            return None

        return DetectionEvaluationRunManifest.model_validate(data)

    def list_sample_evaluations(
        self,
        evaluation_run_id: str,
    ) -> list[DetectionSampleEvaluation]:
        samples_root = (
            evaluation_run_root(
                runs_root=self.runs_root,
                evaluation_run_id=evaluation_run_id,
            )
            / "samples"
        )

        if not samples_root.exists():
            return []

        samples: list[DetectionSampleEvaluation] = []

        for sample_file in sorted(samples_root.glob("*.json")):
            data = self._read_json_or_none(sample_file)
            if data is None:
                continue

            samples.append(DetectionSampleEvaluation.model_validate(data))

        return samples

    def get_sample_evaluation(
        self,
        evaluation_run_id: str,
        sample_id: str,
    ) -> DetectionSampleEvaluation | None:
        path = sample_evaluation_manifest_path(
            runs_root=self.runs_root,
            evaluation_run_id=evaluation_run_id,
            sample_id=sample_id,
        )

        data = self._read_json_or_none(path)
        if data is None:
            return None

        return DetectionSampleEvaluation.model_validate(data)

    def _evaluation_root(self, evaluation_run_id: str) -> Path:
        return self.runs_root / "evaluations" / evaluation_run_id

    def _read_json_or_none(self, path: Path) -> Any | None:
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)


def _matches_filters(
    *,
    evaluation: DetectionEvaluationRunManifest,
    dataset_id: str | None,
    dataset_version: str | None,
    model_id: str | None,
    model_version: str | None,
    inference_run_id: str | None,
    status: str | None,
) -> bool:
    if dataset_id is not None and evaluation.datasetId != dataset_id:
        return False

    if dataset_version is not None and evaluation.datasetVersion != dataset_version:
        return False

    if model_id is not None and evaluation.modelId != model_id:
        return False

    if model_version is not None and evaluation.modelVersion != model_version:
        return False

    if inference_run_id is not None and evaluation.inferenceRunId != inference_run_id:
        return False

    if status is not None and evaluation.status != status:
        return False

    return True
