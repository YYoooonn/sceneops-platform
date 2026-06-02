from __future__ import annotations

from typing import Any

from sceneops_core.evaluations import (
    EvaluationComparisonResponse,
    EvaluationLeaderboardResponse,
    EvaluationRunSummaryItem,
    EvaluationTaskType,
    LeaderboardItem,
    LeaderboardSortBy,
    ModelVersionEvaluationHistoryResponse,
    get_metric_specs_for_task,
    is_descending_sort,
)
from sceneops_core.runs.schemas import EvaluationRunRecord, RunStatus
from sceneops_db.runs import PostgresEvaluationRunRepository


class EvaluationQueryService:
    def __init__(
        self,
        *,
        evaluation_run_repository: PostgresEvaluationRunRepository,
    ) -> None:
        self.evaluation_run_repository = evaluation_run_repository

    async def compare_by_dataset(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        task_type: EvaluationTaskType = EvaluationTaskType.DETECTION,
        evaluator_id: str | None = None,
        status: RunStatus | None = RunStatus.SUCCEEDED,
        limit: int | None = None,
    ) -> EvaluationComparisonResponse:
        records = await self.evaluation_run_repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            evaluator_id=evaluator_id,
            status=status,
            limit=limit,
        )

        return EvaluationComparisonResponse(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            task_type=task_type,
            metric_specs=get_metric_specs_for_task(task_type),
            runs=[
                self._to_summary_item(record, task_type=task_type) for record in records
            ],
        )

    async def list_by_model_version(
        self,
        *,
        model_id: str,
        model_version: str,
        task_type: EvaluationTaskType | None = None,
        status: RunStatus | None = RunStatus.SUCCEEDED,
        limit: int | None = None,
    ) -> ModelVersionEvaluationHistoryResponse:
        records = await self.evaluation_run_repository.list(
            model_id=model_id,
            model_version=model_version,
            status=status,
            limit=limit,
        )

        return ModelVersionEvaluationHistoryResponse(
            model_id=model_id,
            model_version=model_version,
            task_type=task_type,
            runs=[
                self._to_summary_item(
                    record,
                    task_type=task_type or EvaluationTaskType.DETECTION,
                )
                for record in records
            ],
        )

    async def detection_leaderboard(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        sort_by: LeaderboardSortBy = LeaderboardSortBy.PRECISION,
        evaluator_id: str | None = "center-distance",
        limit: int | None = 50,
    ) -> EvaluationLeaderboardResponse:
        task_type = EvaluationTaskType.DETECTION

        records = await self.evaluation_run_repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            evaluator_id=evaluator_id,
            status=RunStatus.SUCCEEDED,
            limit=None,
        )

        items = [
            self._to_summary_item(record, task_type=task_type) for record in records
        ]

        descending = is_descending_sort(
            task_type=task_type,
            sort_by=sort_by,
        )

        sorted_items = sorted(
            items,
            key=lambda item: self._sort_value(item=item, sort_by=sort_by),
            reverse=descending,
        )

        if limit is not None:
            sorted_items = sorted_items[:limit]

        leaderboard_items = [
            LeaderboardItem(
                **item.model_dump(mode="json"),
                rank=index + 1,
                sort_value=self._sort_value(item=item, sort_by=sort_by),
            )
            for index, item in enumerate(sorted_items)
        ]

        return EvaluationLeaderboardResponse(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            task_type=task_type,
            sort_by=sort_by,
            metric_specs=get_metric_specs_for_task(task_type),
            items=leaderboard_items,
        )

    def _to_summary_item(
        self,
        record: EvaluationRunRecord,
        *,
        task_type: EvaluationTaskType,
    ) -> EvaluationRunSummaryItem:
        metrics = self._normalize_metrics(record.metrics or {})

        return EvaluationRunSummaryItem(
            evaluation_run_id=record.id,
            inference_run_id=record.inference_run_id,
            dataset_id=record.dataset_id,
            dataset_version=record.dataset_version,
            model_id=record.model_id,
            model_version=record.model_version,
            evaluator_id=record.evaluator_id,
            task_type=task_type,
            status=record.status,
            sample_count=record.sample_count,
            evaluation_manifest_uri=record.evaluation_manifest_uri,
            metrics=metrics,
            class_metrics=record.class_metrics or {},
            metadata=record.metadata or {},
            created_at=(
                record.created_at.isoformat() if record.created_at is not None else None
            ),
            started_at=(
                record.started_at.isoformat() if record.started_at is not None else None
            ),
            finished_at=(
                record.finished_at.isoformat()
                if record.finished_at is not None
                else None
            ),
        )

    def _normalize_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        return {
            "precision": metrics.get("precision"),
            "recall": metrics.get("recall"),
            "mean_center_distance_error": metrics.get("mean_center_distance_error"),
            "tp": metrics.get("tp"),
            "fp": metrics.get("fp"),
            "fn": metrics.get("fn"),
        }

    def _sort_value(
        self,
        *,
        item: EvaluationRunSummaryItem,
        sort_by: LeaderboardSortBy,
    ) -> Any:
        if sort_by == LeaderboardSortBy.SAMPLE_COUNT:
            return item.sample_count or 0

        if sort_by == LeaderboardSortBy.CREATED_AT:
            return item.created_at or ""

        value = item.metrics.get(sort_by.value)

        if value is None:
            return float("-inf")

        return value
