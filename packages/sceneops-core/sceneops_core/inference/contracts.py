# packages/sceneops-core/sceneops_core/inference/contracts.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.common.types import (
    DatasetId,
    DatasetVersion,
    JsonDict,
    Metadata,
    ModelId,
    ModelVersion,
)


@runtime_checkable
class InferenceBackend(Protocol):
    """Contract for model inference backends.

    Implementations may use:
    - mock backend
    - ONNX Runtime
    - external HTTP inference server
    - Triton Inference Server
    """

    @property
    def backend_type(self) -> str:
        ...

    def predict(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        model_id: ModelId,
        model_version: ModelVersion,
        params: Metadata,
    ) -> JsonDict:
        """Run inference and return prediction manifest-like output."""
