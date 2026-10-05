"""Shared publication of a detection run's pinned output (ADR-007 §33.5).

Every backend computes per-sample predictions; this module writes them as
checksum-pinned shards and publishes the run's prediction manifest revision
write-once. The manifest pins the sample view revisions and sample ids that
ran, the scenario set revision and the explicit configuration, so an
evaluation can name and verify the exact revision it scored.
"""

from __future__ import annotations

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.inference.schemas import (
    DetectionInferenceResult,
    DetectionPredictionManifest,
    DetectionPredictionShardRef,
)

from .base import BackendRun, DetectionInferenceRequest


def _lifting_counts(predictions: list[dict]) -> tuple[int, int, int]:
    succeeded = failed = not_applicable = 0
    for prediction in predictions:
        status = prediction.get("lifting_status", "not_applicable")
        if status == "succeeded":
            succeeded += 1
        elif status == "failed":
            failed += 1
        else:
            not_applicable += 1
    return succeeded, failed, not_applicable


async def publish_predictions(
    request: DetectionInferenceRequest, run: BackendRun
) -> DetectionInferenceResult:
    spec = request.input
    store = request.run_artifact_store

    shards: list[DetectionPredictionShardRef] = []
    succeeded = failed = not_applicable = prediction_count = 0
    for item in sorted(
        run.samples, key=lambda s: (s.sample.scene_id, s.sample.sample_id)
    ):
        document = {
            "run_id": spec.run_id,
            "scene_id": item.sample.scene_id,
            "sample_id": item.sample.sample_id,
            "anchor_observation_id": item.sample.camera.observation.observation_id,
            "predictions": item.predictions,
            "metadata": item.metadata,
        }
        data = canonical_json_bytes(document)
        uri = await store.write_prediction_shard(
            run_id=spec.run_id,
            scene_id=item.sample.scene_id,
            sample_id=item.sample.sample_id,
            data=data,
        )
        shards.append(
            DetectionPredictionShardRef(
                scene_id=item.sample.scene_id,
                sample_id=item.sample.sample_id,
                uri=uri,
                checksum=sha256_checksum(data),
                prediction_count=len(item.predictions),
            )
        )
        s, f, n = _lifting_counts(item.predictions)
        succeeded, failed, not_applicable = (
            succeeded + s,
            failed + f,
            not_applicable + n,
        )
        prediction_count += len(item.predictions)

    manifest = DetectionPredictionManifest(
        inference_run_id=spec.run_id,
        dataset_id=spec.dataset_id,
        dataset_version=spec.dataset_version,
        config=spec.config.model_dump(mode="json"),
        inputs=spec.inputs,
        scenario_set=spec.scenario_set,
        scene_count=len({s.scene_id for s in shards}),
        sample_count=len(shards),
        prediction_count=prediction_count,
        evaluable_prediction_count=prediction_count - failed,
        lifting_succeeded_count=succeeded,
        lifting_failed_count=failed,
        lifting_not_applicable_count=not_applicable,
        prediction_shards=shards,
    )
    checksum = manifest.checksum()
    published = await request.derived_store.publish(
        uri=request.derived_store.prediction_manifest_uri(
            inference_run_id=spec.run_id, checksum=checksum
        ),
        data=manifest.to_canonical_bytes(),
    )
    metrics = {
        "scene_count": manifest.scene_count,
        "sample_count": manifest.sample_count,
        "inference_request_count": (
            run.inference_request_count
            if run.inference_request_count is not None
            else manifest.sample_count
        ),
        "prediction_count": prediction_count,
        "evaluable_prediction_count": manifest.evaluable_prediction_count,
        "lifting_succeeded_count": succeeded,
        "lifting_failed_count": failed,
        "lifting_not_applicable_count": not_applicable,
        **run.metrics,
    }
    return DetectionInferenceResult(
        run_id=spec.run_id,
        prediction_manifest_uri=published.uri,
        prediction_manifest_checksum=checksum,
        predictions_root_uri=store.inference_predictions_root_uri(spec.run_id),
        scene_count=manifest.scene_count,
        sample_count=manifest.sample_count,
        inference_request_count=metrics["inference_request_count"],
        prediction_count=prediction_count,
        evaluable_prediction_count=manifest.evaluable_prediction_count,
        lifting_succeeded_count=succeeded,
        lifting_failed_count=failed,
        status="succeeded",
        metrics=metrics,
        metadata={"backend": spec.config.inference_backend},
    )


__all__ = ["publish_predictions"]
