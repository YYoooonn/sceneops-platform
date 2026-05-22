from app.modules.evaluations.repository import EvaluationRunRepository


class EvaluationRunService:
    def __init__(self, repository: EvaluationRunRepository) -> None:
        self.repository = repository

    def list_evaluations(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        status: str | None = None,
    ):
        return self.repository.list_evaluations(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            inference_run_id=inference_run_id,
            status=status,
        )

    def get_evaluation(self, evaluation_run_id: str):
        return self.repository.get_evaluation(evaluation_run_id)

    def list_sample_evaluations(self, evaluation_run_id: str):
        return self.repository.list_sample_evaluations(evaluation_run_id)

    def get_sample_evaluation(self, evaluation_run_id: str, sample_id: str):
        return self.repository.get_sample_evaluation(evaluation_run_id, sample_id)
