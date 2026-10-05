from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

InferenceRequestT = TypeVar("InferenceRequestT", contravariant=True)
InferenceResultT = TypeVar("InferenceResultT", covariant=True)


@runtime_checkable
class InferenceBackend(Protocol, Generic[InferenceRequestT, InferenceResultT]):
    """Port-like contract for model inference backends.

    Concrete implementations: the mock backend and the HTTP client of the
    external inference server.

    The request/result types are generic because each task type can have
    task-specific input/output payloads while sharing the same backend contract.
    """

    @property
    def backend_type(self) -> str:
        """Stable backend identifier, e.g. mock, grounding_dino."""

    async def run(self, request: InferenceRequestT) -> InferenceResultT:
        """Run inference and return a task-specific inference result."""
