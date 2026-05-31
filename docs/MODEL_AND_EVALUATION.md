# Model and Evaluation Contracts

This document defines the target contract for model registry, inference, prediction manifests, evaluation reports, and comparison.

## Model registry

A model is a logical model family.

```json
{
  "modelId": "dummy-detector",
  "taskType": "detection",
  "name": "Dummy Detector",
  "description": "Detector model used for SceneOps E2E tests",
  "metadata": {
    "domain": "autonomous-driving"
  }
}
```

## Model version

A model version is a concrete executable model artifact or runtime configuration.

```json
{
  "modelId": "dummy-detector",
  "version": "v0",
  "backend": "onnx_runtime",
  "modelUri": "local://models/dummy-detector/versions/v0/model.onnx",
  "status": "ready",
  "metadata": {
    "inputSchema": {
      "type": "tensor",
      "dtype": "float32",
      "shape": [1, 10]
    },
    "outputSchema": {
      "type": "detection_boxes"
    }
  }
}
```

## Inference backend contract

Target interface:

```text
DetectionModelRunner
  predict(sample_batch) -> DetectionPredictionBatch
```

Backends:

```text
mock
onnx_runtime
triton
```

Each backend should implement the same prediction contract.

## Prediction workflow

```text
dataset sample
  -> load sensor artifacts
  -> preprocess
  -> run inference backend
  -> postprocess
  -> export prediction manifest
  -> create inference run
```

## Prediction manifest

Target manifest shape:

```json
{
  "schemaVersion": "sceneops.prediction_manifest.v1",
  "inferenceRunId": "infer_xxx",
  "datasetId": "nuscenes",
  "datasetVersion": "v1.0-mini",
  "modelId": "dummy-detector",
  "modelVersion": "v0",
  "backend": "onnx_runtime",
  "predictions": [
    {
      "sampleId": "sample-0001",
      "objects": [
        {
          "category": "vehicle.car",
          "score": 0.91,
          "translation": [0.0, 0.0, 0.0],
          "size": [1.0, 1.0, 1.0],
          "rotation": [1.0, 0.0, 0.0, 0.0]
        }
      ]
    }
  ],
  "summary": {
    "sampleCount": 1,
    "predictionCount": 1
  }
}
```

## Evaluation workflow

```text
prediction manifest
  -> load ground truth from dataset manifest
  -> match predictions to annotations
  -> compute metrics
  -> export evaluation report
  -> create evaluation run
```

## Evaluation report

Target report shape:

```json
{
  "schemaVersion": "sceneops.evaluation_report.v1",
  "evaluationRunId": "eval_xxx",
  "inferenceRunId": "infer_xxx",
  "datasetId": "nuscenes",
  "datasetVersion": "v1.0-mini",
  "modelId": "dummy-detector",
  "modelVersion": "v0",
  "evaluatorId": "center-distance",
  "params": {
    "matchDistanceM": 2.0
  },
  "metrics": {
    "precision": 0.5,
    "recall": 0.4,
    "f1": 0.44,
    "mAP": 0.3
  },
  "perClassMetrics": {
    "vehicle.car": {
      "precision": 0.6,
      "recall": 0.5
    }
  },
  "summary": {
    "sampleCount": 100,
    "predictionCount": 320,
    "groundTruthCount": 400
  }
}
```

## Model comparison

Target comparison output:

```json
{
  "datasetId": "nuscenes",
  "datasetVersion": "v1.0-mini",
  "taskType": "detection",
  "models": [
    {
      "modelId": "mock-detector",
      "modelVersion": "v0",
      "backend": "mock",
      "metrics": {
        "precision": 0.42,
        "recall": 0.31,
        "mAP": 0.28
      }
    },
    {
      "modelId": "dummy-detector",
      "modelVersion": "v0",
      "backend": "onnx_runtime",
      "metrics": {
        "precision": 0.51,
        "recall": 0.37,
        "mAP": 0.34
      }
    }
  ]
}
```

## Target APIs

```text
GET /api/v1/models/{model_id}/versions/{version}/evaluations
GET /api/v1/evaluations/compare
GET /api/v1/leaderboards/detection
```

## Evaluation design goals

Evaluation should be:

```text
reproducible
dataset-version aware
model-version aware
artifact-backed
configurable
comparable
inspectable
```

## Future model lifecycle integration

Possible integrations:

```text
MLflow Tracking
MLflow Model Registry
Triton Model Repository
Prometheus model-serving metrics
```
