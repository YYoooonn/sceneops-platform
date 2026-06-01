# packages/sceneops-core/sceneops_core/evaluations/contracts.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.common.types import DatasetId, DatasetVersion, JsonDict, Metadata, RunId


@runtime_checkable
class Evaluator(Protocol):
    """Contract for evaluation logic.

    Examples:
    - detection evaluator
    - tracking evaluator
    - segmentation evaluator
    - future robotics-task evaluator
    """

    @property
    def evaluator_id(self) -> str:
        ...

    def evaluate(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        inference_run_id: RunId,
        params: Metadata,
    ) -> JsonDict:
        """Evaluate predictions and return evaluation manifest-like output."""
