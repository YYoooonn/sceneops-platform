# Dataset and Artifact Contracts

This document defines the target contract for dataset, manifest, validation, profiling, and artifact lineage.

## Dataset registry

A dataset is a logical data source.

```json
{
  "id": "nuscenes",
  "datasetType": "nuscenes",
  "name": "nuScenes",
  "description": "nuScenes autonomous driving dataset",
  "metadata": {
    "domain": "autonomous-driving",
    "modality": ["camera", "lidar"]
  }
}
```

## Dataset version

A dataset version is a concrete versioned source.

```json
{
  "datasetId": "nuscenes",
  "version": "v1.0-mini",
  "datasetType": "nuscenes",
  "sourceUri": "local://raw/nuscenes",
  "manifestUri": "local://datasets/nuscenes/v1.0-mini/manifest.json",
  "metadata": {
    "split": "mini"
  }
}
```

## Sensor-centric dataset manifest

Target manifest shape:

```json
{
  "schemaVersion": "sceneops.dataset_manifest.v1",
  "datasetId": "nuscenes",
  "datasetVersion": "v1.0-mini",
  "datasetType": "nuscenes",
  "createdAt": "2026-01-01T00:00:00Z",
  "scenes": [
    {
      "sceneId": "scene-0001",
      "sceneToken": "token",
      "samples": [
        {
          "sampleId": "sample-0001",
          "sampleToken": "token",
          "timestamp": 123456789,
          "egoPose": {
            "translation": [0.0, 0.0, 0.0],
            "rotation": [1.0, 0.0, 0.0, 0.0]
          },
          "sensors": {
            "CAM_FRONT": {
              "sensorType": "camera",
              "uri": "local://raw/nuscenes/samples/CAM_FRONT/xxx.jpg",
              "timestamp": 123456789,
              "calibrationToken": "calib-token"
            },
            "LIDAR_TOP": {
              "sensorType": "lidar",
              "uri": "local://raw/nuscenes/samples/LIDAR_TOP/xxx.bin",
              "timestamp": 123456789,
              "calibrationToken": "calib-token"
            }
          },
          "annotations": {
            "objects": [
              {
                "annotationId": "ann-0001",
                "category": "vehicle.car",
                "translation": [0.0, 0.0, 0.0],
                "size": [1.0, 1.0, 1.0],
                "rotation": [1.0, 0.0, 0.0, 0.0]
              }
            ]
          }
        }
      ]
    }
  ],
  "statistics": {
    "sceneCount": 1,
    "sampleCount": 1,
    "sensorChannels": ["CAM_FRONT", "LIDAR_TOP"]
  }
}
```

## Dataset validation report

Validation should verify that the dataset is usable for model workflows.

```json
{
  "schemaVersion": "sceneops.dataset_validation_report.v1",
  "datasetId": "nuscenes",
  "datasetVersion": "v1.0-mini",
  "status": "passed",
  "checks": [
    {
      "name": "required_sensor_channels",
      "status": "passed",
      "details": {
        "required": ["CAM_FRONT", "LIDAR_TOP"],
        "missingSampleCount": 0
      }
    },
    {
      "name": "sensor_files_exist",
      "status": "passed",
      "details": {
        "missingFileCount": 0
      }
    },
    {
      "name": "annotation_validity",
      "status": "passed",
      "details": {
        "invalidAnnotationCount": 0
      }
    }
  ],
  "summary": {
    "totalChecks": 3,
    "passedChecks": 3,
    "failedChecks": 0
  }
}
```

## Dataset profile report

Profiling should summarize dataset characteristics for model development.

```json
{
  "schemaVersion": "sceneops.dataset_profile_report.v1",
  "datasetId": "nuscenes",
  "datasetVersion": "v1.0-mini",
  "summary": {
    "sceneCount": 2,
    "sampleCount": 80,
    "sensorChannels": ["CAM_FRONT", "LIDAR_TOP"]
  },
  "classDistribution": {
    "vehicle.car": 100,
    "human.pedestrian.adult": 24
  },
  "sensorCompleteness": {
    "CAM_FRONT": 1.0,
    "LIDAR_TOP": 1.0
  }
}
```

## Artifact URI convention

Initial local convention:

```text
local://raw/nuscenes
local://datasets/nuscenes/v1.0-mini/manifest.json
local://datasets/nuscenes/v1.0-mini/validation_report.json
local://datasets/nuscenes/v1.0-mini/profile_report.json
local://runs/inference/{inference_run_id}/predictions.json
local://runs/evaluations/{evaluation_run_id}/report.json
local://models/{model_id}/versions/{version}/model.onnx
```

Future object-storage convention:

```text
s3://sceneops/raw/nuscenes
s3://sceneops/datasets/nuscenes/v1.0-mini/manifest.json
s3://sceneops/runs/inference/{inference_run_id}/predictions.json
s3://sceneops/runs/evaluations/{evaluation_run_id}/report.json
s3://sceneops/models/{model_id}/versions/{version}/model.onnx
```

## Artifact metadata

Target metadata:

```json
{
  "artifactId": "artifact_xxx",
  "artifactType": "prediction_manifest",
  "uri": "local://runs/inference/infer_xxx/predictions.json",
  "contentType": "application/json",
  "sizeBytes": 12345,
  "checksum": "sha256:...",
  "createdBy": {
    "pipelineRunId": "pipe_xxx",
    "jobId": "job_xxx",
    "inferenceRunId": "infer_xxx"
  }
}
```

## Artifact lineage

Target lineage:

```text
DatasetVersion
  -> DatasetManifest
  -> PipelineRun
  -> InferenceRun
  -> PredictionManifest
  -> EvaluationRun
  -> EvaluationReport
```

This lineage is required for reproducible model evaluation.
