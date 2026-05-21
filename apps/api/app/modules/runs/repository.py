from typing import Protocol

from app.modules.runs.schemas import InferenceRunManifest, PredictionManifest


class InferenceRunRepository(Protocol):
    def list_inference_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: str | None = None,
    ) -> list[InferenceRunManifest]: ...

    def get_inference_run(self, run_id: str) -> InferenceRunManifest | None: ...

    def list_predictions(
        self,
        run_id: str,
    ) -> list[PredictionManifest]: ...

    def get_prediction(
        self,
        run_id: str,
        sample_id: str,
    ) -> PredictionManifest | None: ...
