from __future__ import annotations

from sceneops_core.evaluations.schemas.enums import EvaluationTaskType
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.evaluations.schemas.summaries import EvaluationRunSummaryItem
from sceneops_core.runs.schemas import RunStatus
from sceneops_db.repositories.evaluations import EvaluationRunRepository

from app.views.leaderboards.schemas import (
    DatasetVersionEvaluationResponse,
    EvaluationLeaderboardEntry,
    LeaderboardResponse,
    ModelEvaluationHistoryResponse,
)


def _to_summary_item(run: EvaluationRunRecord) -> EvaluationRunSummaryItem:
    return EvaluationRunSummaryItem(
        evaluation_run_id=run.run_id,
        inference_run_id=run.inference_run_id,
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        model_id=run.model_id,
        model_version=run.model_version,
        evaluator_id=run.evaluator_id,
        task_type=run.task_type,
        status=run.status,
        sample_count=run.sample_count,
        evaluation_manifest_uri=run.evaluation_manifest_uri,
        metrics_uri=run.metrics_uri,
        metrics=run.metrics,
        class_metrics=run.class_metrics,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _extract_metric(run: EvaluationRunRecord, metric_name: str | None) -> float | None:
    if not metric_name:
        return None
    val = run.metrics.get(metric_name) or run.summary.get(metric_name)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class LeaderboardService:
    def __init__(self, *, repository: EvaluationRunRepository) -> None:
        self._repository = repository

    async def list_evaluations(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        task_type: EvaluationTaskType | None = None,
        evaluator_id: str | None = None,
        status: RunStatus | None = None,
        metric_name: str | None = None,
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> LeaderboardResponse:
        runs = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            task_type=task_type,
            evaluator_id=evaluator_id,
            status=status,
            limit=limit,
            offset=offset,
        )

        entries = [
            EvaluationLeaderboardEntry(
                evaluation_run_id=run.run_id,
                inference_run_id=run.inference_run_id,
                dataset_id=run.dataset_id,
                dataset_version=run.dataset_version,
                model_id=run.model_id,
                model_version=run.model_version,
                evaluator_id=run.evaluator_id,
                status=run.status,
                metric_name=metric_name,
                metric_value=_extract_metric(run, metric_name),
                summary=run.summary,
                metrics=run.metrics,
                created_at=run.created_at,
                finished_at=run.finished_at,
            )
            for run in runs
        ]

        if metric_name:
            descending = order.lower() != "asc"
            entries.sort(
                key=lambda e: (
                    e.metric_value is None,
                    -(e.metric_value or 0) if descending else (e.metric_value or 0),
                ),
            )

        return LeaderboardResponse(
            entries=entries,
            count=len(entries),
            metric_name=metric_name,
            order=order,
        )

    async def get_model_history(
        self,
        model_id: str,
        *,
        model_version: str | None = None,
        task_type: EvaluationTaskType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ModelEvaluationHistoryResponse:
        runs = await self._repository.list(
            model_id=model_id,
            model_version=model_version,
            task_type=task_type,
            limit=limit,
            offset=offset,
        )
        return ModelEvaluationHistoryResponse(
            model_id=model_id,
            model_version=model_version,
            runs=[_to_summary_item(r) for r in runs],
            count=len(runs),
        )

    async def get_dataset_version_evaluations(
        self,
        dataset_id: str,
        dataset_version: str,
        *,
        task_type: EvaluationTaskType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DatasetVersionEvaluationResponse:
        runs = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            task_type=task_type,
            limit=limit,
            offset=offset,
        )
        return DatasetVersionEvaluationResponse(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            runs=[_to_summary_item(r) for r in runs],
            count=len(runs),
        )
