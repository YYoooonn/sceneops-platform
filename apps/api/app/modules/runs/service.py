from app.modules.runs.repository import InferenceRunRepository


class InferenceRunService:
    def __init__(self, repository: InferenceRunRepository) -> None:
        self.repository = repository

    def list_inference_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: str | None = None,
    ):
        return self.repository.list_inference_runs(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            status=status,
        )

    def get_inference_run(self, run_id: str):
        return self.repository.get_inference_run(run_id)

    def list_predictions(self, run_id: str):
        return self.repository.list_predictions(run_id)

    def get_prediction(self, run_id: str, sample_id: str):
        return self.repository.get_prediction(run_id, sample_id)
