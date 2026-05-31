# Dataset and Artifact Contracts

SceneOps treats a dataset version as a versioned robotics sensor-data source that can be ingested, validated, profiled, and used for model inference/evaluation.

---

## Current dataset lifecycle

```text
registered
  -> ingesting
  -> ingested
  -> validating
  -> ready
```

Failure state:

```text
failed
```

A dataset version becomes usable by prediction/evaluation only after validation succeeds and the status becomes `ready`.

---

## Current dataset ingestion contract

Current job type:

```text
ingest_dataset
```

Current parameters:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "dataset_type": "nuscenes",
  "source_uri": "/data/raw/nuscenes",
  "max_scenes": 2,
  "mode": "upsert"
}
```

Current output:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "dataset_type": "nuscenes",
  "dataset_manifest_uri": "...",
  "scene_count": 2,
  "sample_count": 80,
  "result_summary": {
    "source": "nuscenes",
    "status": "ingested",
    "annotation_count": 0,
    "target_channels": ["CAM_FRONT", "LIDAR_TOP"]
  }
}
```

---

## Current dataset validation contract

Current job type:

```text
validate_dataset_manifest
```

Current parameters:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
  "validate_samples": true,
  "max_samples": 2
}
```

Current validation checks:

| Check | Status |
|---|---|
| dataset manifest exists | implemented |
| scene index exists | implemented |
| scene manifests exist | implemented |
| sample manifests exist | implemented |
| required sensor channels exist in sample manifests | implemented |
| missing scene IDs | implemented |
| missing sample IDs | implemented |
| missing required channels by sample | implemented |
| camera-LiDAR timestamp sync | target |
| calibration / ego-pose validity | target |
| class distribution profiling | target |
| sensor completeness ratio | target |

Current output:

```json
{
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "dataset_manifest_uri": "...",
  "scene_count": 2,
  "sample_count": 80,
  "annotation_count": 0,
  "validated_scene_count": 2,
  "validated_sample_count": 80,
  "missing_sample_count": 0,
  "status": "ready",
  "result_summary": {
    "missing_scene_ids": [],
    "missing_sample_ids": [],
    "missing_channels": {}
  }
}
```

---

## Current artifact types

| Artifact | Current role |
|---|---|
| Dataset manifest | Dataset-level metadata, summary, root URIs, channel information |
| Scene index | List of scene manifests for a dataset version |
| Scene manifest | Scene-level sample sequence metadata |
| Sample manifest | Sample-level sensor and annotation metadata |
| Prediction manifest | Per-sample prediction output |
| Inference run manifest | Inference run metadata and artifact root |
| Evaluation sample manifest | Per-sample evaluation result |
| Evaluation run manifest | Aggregate metrics and class metrics |

---

## Recommended next contract: Dataset Profile Report

This is the highest-impact next addition for the AI Robotics Engineer application.

New job type target:

```text
profile_dataset
```

Target report:

```json
{
  "schema_version": "sceneops.dataset_profile_report.v1",
  "dataset_id": "nuscenes",
  "dataset_version": "v1.0-mini",
  "summary": {
    "scene_count": 2,
    "sample_count": 80,
    "annotation_count": 320
  },
  "sensor_completeness": {
    "CAM_FRONT": 1.0,
    "LIDAR_TOP": 1.0
  },
  "class_distribution": {
    "vehicle.car": 124,
    "human.pedestrian.adult": 18,
    "movable_object.barrier": 9
  },
  "sample_statistics": {
    "empty_annotation_sample_count": 3,
    "avg_annotations_per_sample": 4.0,
    "max_annotations_per_sample": 18
  },
  "quality_summary": {
    "missing_file_count": 0,
    "missing_channel_sample_count": 0,
    "invalid_annotation_count": 0
  }
}
```

---

## Recommended next quality gate

Validation should produce a report artifact that can explain why a pipeline is allowed or blocked.

Target:

```json
{
  "schema_version": "sceneops.dataset_validation_report.v1",
  "status": "passed",
  "should_block_pipeline": false,
  "checks": [
    {
      "name": "required_sensor_channels",
      "status": "passed",
      "details": {
        "required": ["CAM_FRONT", "LIDAR_TOP"],
        "missing_channels": {}
      }
    }
  ]
}
```

Blocked example:

```json
{
  "status": "failed",
  "should_block_pipeline": true,
  "failure_summary": "3 samples are missing LIDAR_TOP",
  "checks": [
    {
      "name": "required_sensor_channels",
      "status": "failed",
      "details": {
        "missing_channels": {
          "sample_001": ["LIDAR_TOP"],
          "sample_002": ["LIDAR_TOP"],
          "sample_003": ["LIDAR_TOP"]
        }
      }
    }
  ]
}
```

---

## Why this matters for robotics AI

Robotics model performance depends heavily on dataset quality. SceneOps should make dataset quality visible before model inference starts.

The target direction is:

```text
raw sensor data
  -> dataset manifest
  -> validation report
  -> profile report
  -> quality gate
  -> inference/evaluation
```
