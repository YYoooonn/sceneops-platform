from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

from sceneops_core.evaluations.schemas.enums import EvaluationTaskType

EvaluationRequestT = TypeVar("EvaluationRequestT", contravariant=True)
EvaluationResultT = TypeVar("EvaluationResultT", covariant=True)


@runtime_checkable
class Evaluator(Protocol, Generic[EvaluationRequestT, EvaluationResultT]):
    """Port-like contract for evaluation logic.

    Concrete implementations may evaluate task-specific outputs such as:
    - detection predictions
    - tracking results
    - segmentation masks
    - auto-label quality
    - dataset validation quality
    - scene reconstruction quality
    - scene comparison results
    - scenario readiness

    The request/result types are generic because each evaluation task can have
    task-specific inputs and outputs while sharing the same evaluator contract.
    """

    @property
    def evaluator_id(self) -> str:
        """Stable evaluator identifier, e.g. center-distance."""

    @property
    def task_type(self) -> EvaluationTaskType:
        """Evaluation task type handled by this evaluator."""

    async def run(self, request: EvaluationRequestT) -> EvaluationResultT:
        """Run evaluation and return a task-specific evaluation result."""
