# E2E Workflows

This document describes the end-to-end workflows that are currently implemented and used to validate SceneOps locally.

The E2E tests are designed to prove that the system works as a pipeline platform, not just as isolated scripts.

```text
API request
  -> PostgreSQL pipeline/job records
  -> Celery dispatch
  -> Worker execution
  -> artifact generation
  -> metadata update
  -> status polling
```

---

## Prerequisites

```bash
make prepare-data
make compose-build
make compose-up
make db-migrate
make check-env
make check-imports
make check-celery
```

Register the dataset fixture before running dataset or model validation workflows:

```bash
make register-nuscenes-dataset
```

---

## E2E 1. Dataset ingestion + validation

Run:

```bash
make e2e-dataset-ingest
```

Pipeline type:

```text
dataset_ingestion
```

Current pipeline steps:

```text
ingest
  -> validate
```

What this validates:

- FastAPI can create a pipeline run.
- Pipeline steps are generated from the built-in pipeline definition.
- Celery dispatch can execute the pipeline asynchronously.
- Worker can ingest a nuScenes dataset version.
- Dataset manifests are written to local artifact storage.
- Dataset version metadata is updated in PostgreSQL.
- Validation checks scene/sample manifest availability.
- Validation checks required sensor channels such as `CAM_FRONT` and `LIDAR_TOP`.
- Dataset version is promoted to `ready` after successful validation.

Expected output:

```text
pipeline status: succeeded
dataset version status: ready
dataset manifest URI: generated
validated scene/sample counts: generated
missing scenes/samples/channels: empty for passing runs
```

---

## E2E 2. Mock detection validation

Run:

```bash
make e2e-mock-celery
```

Pipeline type:

```text
detection_validation
```

Current pipeline steps:

```text
predict
  -> evaluate
```

What this validates:

- Mock detector model can be registered.
- Model version can be registered with `mock` backend.
- Detection validation pipeline can be created.
- Prediction job can load a ready dataset version.
- Prediction job can generate prediction manifests.
- Evaluation job can consume prediction artifacts.
- Evaluation metrics are stored in an evaluation run record.

Expected output:

```text
pipeline status: succeeded
inference run status: succeeded
evaluation run status: succeeded
prediction manifest URI: generated
evaluation manifest URI: generated
```

---

## E2E 3. ONNX Runtime detection validation

Run:

```bash
make e2e-onnx-celery
```

Pipeline type:

```text
detection_validation
```

What this validates:

- Dummy ONNX model artifact can be created inside the worker environment.
- Model version can be registered with `onnx_runtime` backend.
- Prediction job can instantiate the ONNX Runtime backend.
- Detection validation pipeline can run through Celery.
- Evaluation can run on ONNX-generated prediction artifacts.

Expected output:

```text
pipeline status: succeeded
model backend: onnx_runtime
inference run status: succeeded
evaluation run status: succeeded
```

---

## Debug commands

Show all runs:

```bash
make show-runs
```

Show one pipeline:

```bash
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
```

Show job events:

```bash
make show-job-events JOB_ID=job-xxx
```

---

## What these E2Es prove

The current E2E scripts prove the following platform properties:

1. API and worker are separated into control plane and execution plane.
2. PostgreSQL is the source of truth for metadata.
3. Redis/Celery handles asynchronous execution.
4. Dataset, prediction, and evaluation outputs are represented as artifacts.
5. Pipelines are reproducible and traceable through run IDs and job IDs.
6. The model runtime is pluggable enough to support mock and ONNX Runtime backends.

---

## Next E2E targets

High-impact E2E additions before portfolio submission:

```text
1. dataset_profile E2E
   ingest -> validate -> profile

2. model_compare E2E
   run mock detector
   run ONNX detector
   compare evaluation metrics

3. pipeline_timeline E2E
   create pipeline
   dispatch pipeline
   query step/job timeline

4. failed_validation E2E
   run validation with missing required channel
   assert pipeline is blocked with clear failure reason
```
