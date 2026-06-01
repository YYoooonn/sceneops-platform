from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

EvaluationRequestT = TypeVar("EvaluationRequestT", contravariant=True)
EvaluationResultT = TypeVar("EvaluationResultT", covariant=True)


@runtime_checkable
class Evaluator(Protocol, Generic[EvaluationRequestT, EvaluationResultT]):
    """Port-like contract for evaluation logic.

    Concrete implementations may evaluate:
    - detection
    - tracking
    - segmentation
    - trajectory prediction
    - future robotics tasks

    The request/result types are generic because each task type can have
    task-specific inputs and outputs while sharing the same evaluator contract.
    """

    @property
    def evaluator_id(self) -> str:
        """Stable evaluator identifier, e.g. center-distance."""

    async def run(self, request: EvaluationRequestT) -> EvaluationResultT:
        """Run evaluation and return a task-specific evaluation result."""
