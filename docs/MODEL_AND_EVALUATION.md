# Model and Evaluation Contracts

SceneOps treats model inference and evaluation as reproducible pipeline steps with versioned model metadata, prediction artifacts, and evaluation run records.

---

## Current model lifecycle

Current model registry supports:

- model registration
- model version registration
- backend metadata
- model URI metadata
- endpoint URL metadata for future external serving integration

Current supported inference backends:

| Backend | Status | Purpose |
|---|---|---|
| `mock` | implemented | Validate pipeline shape without real model dependency |
| `onnx_runtime` | implemented | Validate model artifact loading and runtime execution |
| `triton` | target | Production-like inference server backend |
| `external_http` | target | Generic inference server adapter |

---

## Current prediction contract

Current job type:

```text
predict_detection
```

Parameters:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "model_id": "dummy-detector",
  "model_version": "v0",
  "inference_backend": "onnx_runtime",
  "model_uri": "/data/models/dummy-detector/versions/v0/model.onnx",
  "max_samples": 2
}
```

Current behavior:

```text
load model version
  -> validate requested backend matches registered backend
  -> load ready dataset version
  -> load dataset manifest
  -> run inference backend
  -> write prediction manifests
  -> upsert inference run record
```

Current output:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "model_id": "dummy-detector",
  "model_version": "v0",
  "inference_run_id": "run_xxx",
  "prediction_manifest_uri": "...",
  "sample_count": 2,
  "result_summary": {
    "prediction_count": 2,
    "backend": "onnx_runtime"
  }
}
```

---

## Current evaluation contract

Current job type:

```text
evaluate_detection
```

Current evaluator:

```text
center-distance
```

Parameters:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "inference_run_id": "run_xxx",
  "evaluator_id": "center-distance",
  "match_distance_m": 2.0
}
```

Current behavior:

```text
load ready dataset version
  -> load inference run
  -> load prediction manifests
  -> match predictions to ground truth by category and center distance
  -> write per-sample evaluation manifests
  -> write aggregate evaluation manifest
  -> upsert evaluation run record
```

Current aggregate metrics:

| Metric | Status |
|---|---|
| TP | implemented |
| FP | implemented |
| FN | implemented |
| precision | implemented |
| recall | implemented |
| mean center distance error | implemented |
| per-class TP/FP/FN | implemented |
| per-class precision/recall | implemented |
| official nuScenes mAP/NDS | target |

Example output:

```json
{
  "evaluation_run_id": "eval_xxx",
  "inference_run_id": "run_xxx",
  "evaluator_id": "center-distance",
  "match_distance_m": 2.0,
  "metrics": {
    "tp": 10,
    "fp": 3,
    "fn": 5,
    "precision": 0.769231,
    "recall": 0.666667,
    "meanCenterDistanceError": 0.84
  },
  "classMetrics": {
    "vehicle.car": {
      "tp": 8,
      "fp": 2,
      "fn": 3,
      "precision": 0.8,
      "recall": 0.727273
    }
  }
}
```

---

## Important scope note

The current evaluator is intentionally a simplified center-distance detection evaluator.

It is not yet an official nuScenes detection benchmark implementation. Its purpose is to validate the MLOps workflow:

```text
model version
  -> inference run
  -> prediction artifact
  -> evaluation run
  -> metrics
  -> future model comparison
```

This keeps the project honest while still demonstrating production-oriented model-evaluation infrastructure.

---

## Recommended next contract: Model comparison

High-impact target endpoint:

```text
GET /api/v1/evaluations/compare?dataset_id=nuscenes&dataset_version=v1.0-mini
```

Target response:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "task_type": "detection",
  "evaluator_id": "center-distance",
  "runs": [
    {
      "model_id": "mock-detector",
      "model_version": "v0",
      "backend": "mock",
      "evaluation_run_id": "eval_mock",
      "metrics": {
        "precision": 0.42,
        "recall": 0.31,
        "meanCenterDistanceError": 1.92
      }
    },
    {
      "model_id": "dummy-detector",
      "model_version": "v0",
      "backend": "onnx_runtime",
      "evaluation_run_id": "eval_onnx",
      "metrics": {
        "precision": 0.51,
        "recall": 0.37,
        "meanCenterDistanceError": 1.48
      }
    }
  ]
}
```

---

## Recommended next contract: Detection leaderboard

High-impact target endpoint:

```text
GET /api/v1/leaderboards/detection?dataset_id=nuscenes&dataset_version=v1.0-mini&sort=precision
```

Target response:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "sort": "precision",
  "items": [
    {
      "rank": 1,
      "model_id": "dummy-detector",
      "model_version": "v0",
      "backend": "onnx_runtime",
      "precision": 0.51,
      "recall": 0.37,
      "meanCenterDistanceError": 1.48
    }
  ]
}
```

---

## Future serving direction

The next runtime abstraction should separate:

```text
preprocess
  -> inference backend
  -> postprocess
  -> prediction export
```

Target backends:

- `mock`
- `onnx_runtime`
- `external_http`
- `triton`

This allows SceneOps to evolve from local batch validation into production-style model serving and evaluation.
