# E2E Workflows

This document describes the end-to-end workflows used to validate SceneOps locally.

## Prerequisites

Start local services:

```bash
make compose-build
make compose-up
make db-migrate
```

Run environment checks:

```bash
make check-env
make check-imports
make check-celery
```

Prepare data directories:

```bash
make prepare-data
```

If using nuScenes mini, place the dataset under:

```text
data/raw/nuscenes/
```

## Services used by E2E tests

The local compose stack runs:

```text
postgres
redis
api
worker-celery
```

The Celery worker listens to:

```text
sceneops.pipeline_runs
sceneops.jobs
```

## 1. Dataset ingestion E2E

Command:

```bash
make e2e-dataset-ingest
```

Script:

```text
scripts/e2e/e2e_ingestion_pipeline_celery.sh
```

Purpose:

Validate dataset ingestion through the full API -> Celery -> Worker -> DB -> artifact path.

Flow:

```text
check compose services
  -> create dataset_ingestion pipeline run
  -> dispatch pipeline through Celery
  -> poll pipeline status
  -> check dataset version registry
  -> print recent worker logs
```

Default parameters:

```text
DATASET_ID=nuscenes
DATASET_VERSION=v1.0-mini
DATASET_TYPE=nuscenes
PIPELINE_TYPE=dataset_ingestion
MAX_SCENES=2
INGEST_MODE=upsert
VALIDATE_SAMPLES=true
REQUIRE_TARGET_CHANNELS_JSON=["CAM_FRONT", "LIDAR_TOP"]
```

Expected result:

```text
pipeline status = succeeded
dataset version registry exists
dataset manifest is generated
worker logs show successful execution
```

## 2. Mock detection validation E2E

Command:

```bash
make e2e-mock-celery
```

Script:

```text
scripts/e2e/e2e_mock_pipeline_celery.sh
```

Purpose:

Validate detection workflow using a mock model backend.

Flow:

```text
check compose services
  -> register mock model fixture
  -> create detection_validation pipeline run
  -> dispatch pipeline through Celery
  -> poll pipeline status
  -> list inference runs
  -> list evaluation runs
  -> print recent worker logs
```

Default parameters:

```text
MODEL_ID=mock-detector
MODEL_VERSION=v0
MODEL_BACKEND=mock
MODEL_TASK_TYPE=detection
PIPELINE_TYPE=detection_validation
MAX_SAMPLES=3
MATCH_DISTANCE_M=2.0
```

Expected result:

```text
pipeline status = succeeded
inference run is created
evaluation run is created
prediction manifest is generated
evaluation metrics are generated
```

## 3. ONNX Runtime detection validation E2E

Command:

```bash
make e2e-onnx-celery
```

Script:

```text
scripts/e2e/e2e_onnx_pipeline_celery.sh
```

Purpose:

Validate detection workflow using an ONNX Runtime model backend.

Flow:

```text
check compose services
  -> create dummy ONNX model artifact
  -> register ONNX model fixture
  -> create detection_validation pipeline run
  -> dispatch pipeline through Celery
  -> poll pipeline status
  -> list inference runs
  -> list evaluation runs
  -> print recent worker logs
```

Default parameters:

```text
MODEL_ID=dummy-detector
MODEL_VERSION=v0
MODEL_BACKEND=onnx_runtime
MODEL_URI=/data/models/dummy-detector/versions/v0/model.onnx
MODEL_TASK_TYPE=detection
PIPELINE_TYPE=detection_validation
MAX_SAMPLES=3
MATCH_DISTANCE_M=2.0
```

Expected result:

```text
dummy ONNX model file exists
model version is registered
pipeline status = succeeded
inference run is created
evaluation run is created
```

## Debug commands

List inference/evaluation runs:

```bash
make show-runs
```

Inspect a pipeline:

```bash
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
```

Inspect job events:

```bash
make show-job-events JOB_ID=job-xxx
```

Inspect worker logs:

```bash
make worker-logs
```

Inspect API logs:

```bash
make api-logs
```

## Common failure cases

### API is not reachable

Check compose state:

```bash
make compose-ps
make api-logs
```

### Celery worker is not consuming jobs

Check broker and worker:

```bash
make check-celery
make worker-logs
```

### Pipeline polling times out

Inspect pipeline and worker logs:

```bash
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
make worker-logs
```

### Dataset files are missing

Check raw dataset location:

```text
data/raw/nuscenes/
```

### Model artifact is missing

For ONNX E2E, the script should create:

```text
/data/models/dummy-detector/versions/v0/model.onnx
```

If missing, rerun:

```bash
make e2e-onnx-celery
```

## E2E success criteria

The local system is considered healthy when all of the following pass:

```bash
make check-env
make check-imports
make check-celery
make e2e-dataset-ingest
make e2e-mock-celery
make e2e-onnx-celery
```
