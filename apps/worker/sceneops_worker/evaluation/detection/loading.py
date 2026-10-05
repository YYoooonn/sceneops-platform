from __future__ import annotations

from typing import Any

from sceneops_core.inference.schemas.manifests import DetectionPredictionShardRef

from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest


async def load_sample_prediction_payload(
    request: DetectionEvaluationRequest, shard: DetectionPredictionShardRef
) -> dict[str, Any]:
    """One sample's predictions, verified against the checksum the pinned
    prediction manifest holds for its shard."""
    payload = await request.run_artifact_store.read_pinned_prediction_shard(
        uri=shard.uri, checksum=shard.checksum
    )
    if (
        payload.get("scene_id") != shard.scene_id
        or payload.get("sample_id") != shard.sample_id
    ):
        raise ValueError(
            f"prediction shard {shard.uri} is for "
            f"{payload.get('scene_id')}/{payload.get('sample_id')}, the manifest "
            f"names {shard.scene_id}/{shard.sample_id}"
        )
    if "predictions" not in payload:
        raise ValueError(f"prediction shard missing 'predictions': {shard.uri}")
    return payload
