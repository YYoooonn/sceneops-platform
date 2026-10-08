"""Mock detection backend: a deterministic test double.

It has no model. For each sample it perturbs the *reference labels* the
sample carries (dropping some, jittering the rest, adding a false positive),
so the detection, evaluation and curation workflows can be exercised end to
end without an inference server. Predictions are seeded by run and sample, so
the same run reproduces the same output.
"""

from __future__ import annotations

import random

from sceneops_core.inference.enums import InferenceBackendType

from .base import (
    BackendRun,
    DetectionInferenceRequest,
    DetectionSampleInput,
    SampleDetectionBackend,
    SamplePrediction,
)

_DROP_PROBABILITY = 0.15
_FALSE_POSITIVE_PROBABILITY = 0.25


class MockDetectionInferenceBackend(SampleDetectionBackend):
    uses_reference_labels = True

    @property
    def backend_type(self) -> str:
        return InferenceBackendType.MOCK.value

    async def predict(self, request: DetectionInferenceRequest) -> BackendRun:
        return BackendRun(
            samples=[
                SamplePrediction(
                    sample=sample,
                    predictions=_predict(request.input.run_id, sample),
                    metadata={"backend": self.backend_type},
                )
                for sample in request.samples
            ]
        )


def _predict(run_id: str, sample: DetectionSampleInput) -> list[dict]:
    rng = random.Random(f"{run_id}:{sample.scene_id}:{sample.sample_id}")
    predictions: list[dict] = []
    for index, label in enumerate(sample.reference_labels):
        if rng.random() < _DROP_PROBABILITY:
            continue
        x, y, z = label.box.center_m
        width, length, height = label.box.size_wlh_m
        predictions.append(
            {
                "prediction_id": f"{sample.sample_id}-pred-{index:04d}",
                "category_name": label.category,
                "frame_id": label.box.frame_id,
                "translation": [
                    round(x + rng.uniform(-0.8, 0.8), 4),
                    round(y + rng.uniform(-0.8, 0.8), 4),
                    round(z + rng.uniform(-0.2, 0.2), 4),
                ],
                "size": [
                    round(max(0.1, v + rng.uniform(-0.2, 0.2)), 4)
                    for v in (width, length, height)
                ],
                "rotation": list(label.box.rotation_wxyz),
                "score": round(rng.uniform(0.55, 0.98), 4),
                "source_label_id": label.label_id,
                "lifting_status": "not_applicable",
            }
        )
    if sample.reference_labels and rng.random() < _FALSE_POSITIVE_PROBABILITY:
        reference = sample.reference_labels[0]
        predictions.append(
            {
                "prediction_id": f"{sample.sample_id}-fp-0000",
                "category_name": reference.category,
                "frame_id": reference.box.frame_id,
                "translation": [
                    round(rng.uniform(-30.0, 30.0), 4),
                    round(rng.uniform(-30.0, 30.0), 4),
                    round(rng.uniform(0.0, 2.0), 4),
                ],
                "size": [4.2, 1.8, 1.6],
                "rotation": [1.0, 0.0, 0.0, 0.0],
                "score": round(rng.uniform(0.3, 0.7), 4),
                "source_label_id": None,
                "lifting_status": "not_applicable",
            }
        )
    return predictions


__all__ = ["MockDetectionInferenceBackend"]
