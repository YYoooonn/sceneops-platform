import json
from pathlib import Path
from typing import Any

from app.modules.runs.schemas import InferenceRunManifest, PredictionManifest


class LocalInferenceRunRepository:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root

    def list_inference_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: str | None = None,
    ) -> list[InferenceRunManifest]:
        inference_root = self.runs_root / "inference"

        if not inference_root.exists():
            return []

        runs: list[InferenceRunManifest] = []

        for run_dir in sorted(inference_root.iterdir()):
            if not run_dir.is_dir():
                continue

            run = self.get_inference_run(run_dir.name)
            if run is None:
                continue

            if not _matches_filters(
                run=run,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                model_id=model_id,
                model_version=model_version,
                status=status,
            ):
                continue

            runs.append(run)

        return runs

    def get_inference_run(self, run_id: str) -> InferenceRunManifest | None:
        path = self._run_root(run_id) / "run.json"

        data = self._read_json_or_none(path)
        if data is None:
            return None

        return InferenceRunManifest.model_validate(data)

    def list_predictions(self, run_id: str) -> list[PredictionManifest]:
        predictions_root = self._run_root(run_id) / "predictions"

        if not predictions_root.exists():
            return []

        predictions: list[PredictionManifest] = []

        for prediction_file in sorted(predictions_root.glob("*.json")):
            data = self._read_json_or_none(prediction_file)
            if data is None:
                continue

            predictions.append(PredictionManifest.model_validate(data))

        return predictions

    def get_prediction(
        self,
        run_id: str,
        sample_id: str,
    ) -> PredictionManifest | None:
        path = self._run_root(run_id) / "predictions" / f"{sample_id}.json"

        data = self._read_json_or_none(path)
        if data is None:
            return None

        return PredictionManifest.model_validate(data)

    def _run_root(self, run_id: str) -> Path:
        return self.runs_root / "inference" / run_id

    def _read_json_or_none(self, path: Path) -> Any | None:
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)


def _matches_filters(
    *,
    run: InferenceRunManifest,
    dataset_id: str | None,
    dataset_version: str | None,
    model_id: str | None,
    model_version: str | None,
    status: str | None,
) -> bool:
    if dataset_id is not None and run.datasetId != dataset_id:
        return False

    if dataset_version is not None and run.datasetVersion != dataset_version:
        return False

    if model_id is not None and run.modelId != model_id:
        return False

    if model_version is not None and run.modelVersion != model_version:
        return False

    if status is not None and run.status != status:
        return False

    return True
