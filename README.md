# SceneOps Platform

SceneOps Platform is a local-first MLOps control plane for robotics perception workflows.

It connects robotics sensor datasets, dataset ingestion, dataset validation, dataset profiling, model inference, detection evaluation, artifact lineage, and asynchronous pipeline execution into one reproducible system.

```text
robotics sensor data
  -> dataset registry
  -> ingestion + manifest generation
  -> dataset validation / quality gate
  -> dataset profiling / quality statistics
  -> model inference
  -> detection evaluation
  -> artifact lineage
  -> model/runtime iteration
```

The current implementation uses **FastAPI**, **PostgreSQL**, **Redis/Celery**, local artifact storage, and pluggable inference backends including **mock** and **ONNX Runtime**.

---

## Why this project exists

Robotics AI systems are not only model-training problems.

A practical robotics AI workflow needs repeatable infrastructure for:

* sensor dataset ingestion and preprocessing
* dataset versioning
* dataset validation and quality gating
* dataset profiling and quality statistics
* model version management
* batch inference execution
* evaluation and metric tracking
* artifact lineage across datasets, predictions, and evaluations
* asynchronous job/pipeline orchestration
* operational visibility for failed or blocked runs

SceneOps is a portfolio-scale implementation of that system, focused on nuScenes-style autonomous-driving sensor data and 3D perception pipeline infrastructure.

---

## What is implemented now

| Area                  | Current implementation                                                                                                                    |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Control plane         | FastAPI API server with dataset, model, job, pipeline, run, and artifact routes                                                           |
| Metadata store        | PostgreSQL-backed repositories with Alembic migrations                                                                                    |
| Async execution       | Redis broker + Celery worker runtime                                                                                                      |
| Dataset registry      | Dataset and DatasetVersion registration with source/manifest metadata                                                                     |
| Dataset ingestion     | nuScenes mini ingestion into dataset, scene, and sample manifests                                                                         |
| Dataset validation    | First-class `validate_dataset` job, validation run record, validation report artifact, and quality gate                                   |
| Dataset profiling     | First-class `profile_dataset` job, profile run record, profile report artifact, sensor coverage, observed channels, annotation statistics |
| Dataset quality cache | Latest validation/profile summary cached on DatasetVersion                                                                                |
| Model registry        | Model and ModelVersion registration with backend/model URI metadata                                                                       |
| Pipelines             | `dataset_ingestion` and `detection_validation` pipeline definitions                                                                       |
| Job runtime           | `ingest_dataset`, `validate_dataset`, `profile_dataset`, `predict_detection`, `evaluate_detection` jobs                                   |
| Inference             | Mock detection backend and ONNX Runtime dummy detector backend                                                                            |
| Evaluation            | Center-distance detection evaluator with TP/FP/FN, precision, recall, mean center distance error, and per-class metrics                   |
| Artifact lineage      | Dataset manifests, validation reports, profile reports, prediction manifests, evaluation manifests, and run records                       |
| E2E scripts           | Dataset ingestion/profile, mock detection validation, and ONNX detection validation E2E scripts                                           |

---

## Current E2E workflows

### 1. Dataset ingestion + validation + profiling

```text
create dataset_ingestion pipeline run
  -> dispatch pipeline through Celery
  -> ingest nuScenes dataset version
  -> write dataset / scene / sample manifests
  -> validate scene and sample manifests
  -> validate required sensor channels, e.g. CAM_FRONT and LIDAR_TOP
  -> write validation_report.json
  -> update dataset_validation_runs
  -> update DatasetVersion latest validation summary
  -> if validation is not blocking, run profile_dataset
  -> compute observed channels, sensor coverage, annotation statistics
  -> write profile_report.json
  -> update dataset_profile_runs
  -> update DatasetVersion latest profile summary
  -> return structured pipeline result with dataset / validation / profile outputs
```

Run:

```bash
make e2e-dataset-ingest
```

Expected steps:

```text
ingest_dataset      succeeded
validate_dataset    succeeded
profile_dataset     succeeded
```

Expected artifacts:

```text
data/datasets/{dataset_id}/{dataset_version}/...
data/runs/dataset_validations/{validation_run_id}/validation_report.json
data/runs/dataset_profiles/{profile_run_id}/profile_report.json
```

Expected pipeline result shape:

```json
{
  "outputs": {
    "dataset": {
      "datasetId": "nuscenes",
      "datasetVersion": "v1.0-mini",
      "manifestUri": "..."
    },
    "validation": {
      "runId": "validation-job-xxx",
      "status": "ready",
      "reportUri": "...",
      "shouldBlockPipeline": false
    },
    "profile": {
      "runId": "profile-job-xxx",
      "reportUri": "...",
      "sensorCoverageRatio": 1.0,
      "emptyAnnotationSampleRatio": 0.0,
      "observedChannels": ["CAM_FRONT", "LIDAR_TOP"]
    }
  }
}
```

### 2. Mock detection validation

```text
register mock detector model
  -> create detection_validation pipeline run
  -> run mock prediction backend
  -> write prediction manifests
  -> evaluate predictions with center-distance evaluator
  -> store inference and evaluation run records
```

Run:

```bash
make e2e-mock-celery
```

### 3. ONNX Runtime detection validation

```text
create dummy ONNX model artifact
  -> register ONNX model version
  -> create detection_validation pipeline run
  -> run ONNX Runtime prediction backend
  -> evaluate predictions
  -> store inference and evaluation artifacts
```

Run:

```bash
make e2e-onnx-celery
```

---

## Architecture

```text
[CLI / E2E Script / Future Dashboard]
        |
        v
[FastAPI Control Plane]
  - Dataset API
  - Model API
  - Pipeline API
  - Job API
  - Run API
  - Artifact API
        |
        v
[PostgreSQL Metadata Store]
  - datasets / dataset_versions
  - dataset_validation_runs / dataset_profile_runs
  - models / model_versions
  - jobs / job_events
  - pipeline_runs / pipeline_step_runs
  - inference_runs / evaluation_runs
        |
        v
[Execution Dispatcher]
  - Celery dispatch backend
        |
        v
[Redis Broker]
        |
        v
[Celery Worker Runtime]
  - PipelineRunner
  - JobRunner
        |
        v
[Executors]
  - dataset ingestion
  - dataset validation
  - dataset profiling
  - prediction
  - evaluation
        |
        v
[Artifact Store]
  - dataset manifests
  - validation reports
  - profile reports
  - prediction manifests
  - evaluation manifests
  - local filesystem now
  - object storage later
        |
        v
[Model Runtime]
  - mock backend
  - ONNX Runtime backend
  - Triton / external inference server later
```

---

## Core domain model

```text
Dataset
  -> DatasetVersion
  -> DatasetManifest

DatasetVersion
  -> DatasetValidationRun
  -> validation_report.json

DatasetVersion
  -> DatasetProfileRun
  -> profile_report.json

Model
  -> ModelVersion

PipelineRun
  -> PipelineStepRun
  -> Job
  -> JobEvent

InferenceRun
  -> PredictionManifest

EvaluationRun
  -> EvaluationManifest
```

### DatasetValidationRun

Validation answers:

```text
Can this DatasetVersion be used by downstream model workflows?
```

Responsibilities:

* validate dataset manifest availability
* validate scene/sample manifest availability
* validate required sensor channels
* detect missing scenes, samples, channels, and artifacts
* produce `validation_report.json`
* decide `should_block_pipeline`

Source of truth:

```text
dataset_validation_runs
data/runs/dataset_validations/{validation_run_id}/validation_report.json
```

Latest summary cache:

```text
dataset_versions.latest_validation_run_id
dataset_versions.validation_status
dataset_versions.should_block_pipeline
dataset_versions.validation_report_uri
```

### DatasetProfileRun

Profiling answers:

```text
What characteristics does this DatasetVersion have?
```

Responsibilities:

* compute observed sensor channels
* compute sensor coverage ratio
* compute missing required channel count
* compute scene/sample/annotation statistics
* compute annotation class distribution
* compute empty annotation sample ratio
* produce `profile_report.json`

Source of truth:

```text
dataset_profile_runs
data/runs/dataset_profiles/{profile_run_id}/profile_report.json
```

Latest summary cache:

```text
dataset_versions.latest_profile_run_id
dataset_versions.profile_report_uri
dataset_versions.sensor_coverage_ratio
dataset_versions.empty_annotation_sample_ratio
dataset_versions.observed_channels
```

---

## Repository structure

```text
sceneops-platform/
  apps/
    api/                 # FastAPI control plane
    worker/              # Celery worker, CLI worker, job/pipeline runtime

  packages/
    sceneops-core/        # shared schemas, enums, ids, contracts
    sceneops-db/          # database models and repositories
    sceneops-storage/     # storage abstraction

  migrations/            # Alembic migrations

  scripts/
    checks/              # local health/import/broker checks
    debug/               # run/job inspection scripts
    e2e/                 # end-to-end validation scripts
    fixtures/            # dataset/model registration fixtures

  data/
    raw/                 # local raw dataset mount
    datasets/            # generated dataset manifests
    runs/                # validation/profile/inference/evaluation outputs
    models/              # local model artifacts

  docker-compose.local.yml
  Makefile
  README.md
```

---

## Quickstart

### 1. Prepare local directories

```bash
make prepare-data
```

Expected directories:

```text
data/raw/
data/datasets/
data/runs/
data/models/
```

Place nuScenes mini under the configured raw data path, for example:

```text
data/raw/nuscenes/
```

### 2. Build and start services

```bash
make compose-build
make compose-up
```

This starts PostgreSQL, Redis, FastAPI, and the Celery worker.

### 3. Run database migrations

```bash
make db-migrate
```

### 4. Check the local environment

```bash
make check-env
make check-imports
make check-celery
```

### 5. Register the nuScenes dataset fixture

```bash
make register-nuscenes-dataset
```

### 6. Run E2E workflows

```bash
make e2e-dataset-ingest
make e2e-mock-celery
make e2e-onnx-celery
```

### 7. Inspect results

```bash
make show-runs
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
make show-job-events JOB_ID=job-xxx
```

Useful DB checks:

```sql
select
  id,
  dataset_id,
  dataset_version,
  status,
  validation_status,
  should_block_pipeline,
  created_at
from dataset_validation_runs
order by created_at desc
limit 10;
```

```sql
select
  id,
  dataset_id,
  dataset_version,
  status,
  sensor_coverage_ratio,
  empty_annotation_sample_ratio,
  created_at
from dataset_profile_runs
order by created_at desc
limit 10;
```

```sql
select
  dataset_id,
  version,
  validation_status,
  latest_validation_run_id,
  latest_profile_run_id,
  sensor_coverage_ratio,
  empty_annotation_sample_ratio
from dataset_versions;
```

---

## Artifact contracts

### Dataset manifest

Dataset manifests are generated by `ingest_dataset`.

```text
data/datasets/{dataset_id}/{dataset_version}/manifest.json
data/datasets/{dataset_id}/{dataset_version}/scene_index.json
data/datasets/{dataset_id}/{dataset_version}/scenes/{scene_id}.json
data/datasets/{dataset_id}/{dataset_version}/samples/{sample_id}.json
```

### Validation report

Validation reports are generated by `validate_dataset`.

```text
data/runs/dataset_validations/{validation_run_id}/validation_report.json
```

Validation is a quality gate. It determines whether a dataset version can be used by downstream workflows.

### Profile report

Profile reports are generated by `profile_dataset`.

```text
data/runs/dataset_profiles/{profile_run_id}/profile_report.json
```

Profiling is not a quality gate. It describes dataset statistics, sensor coverage, and annotation distribution.

### Prediction manifest

Prediction manifests are generated by `predict_detection`.

```text
data/runs/inference/{inference_run_id}/...
```

### Evaluation manifest

Evaluation manifests are generated by `evaluate_detection`.

```text
data/runs/evaluations/{evaluation_run_id}/...
```

---

## Final product goal

The final goal is to build a production-like MLOps platform for robotics perception workflows that can:

1. ingest robotics sensor datasets,
2. validate dataset usability,
3. profile dataset quality and distribution,
4. run model inference through multiple runtime backends,
5. evaluate predictions reproducibly,
6. compare model versions,
7. track artifact lineage,
8. monitor asynchronous pipeline execution,
9. and later connect simulated/counterfactual datasets into the same validation, profiling, and evaluation loop.

---

## Current limitations

Implemented baseline:

* dataset registry
* model registry
* async job/pipeline execution
* dataset ingestion
* dataset validation
* dataset profiling
* mock / ONNX Runtime inference
* center-distance detection evaluation
* local artifact lineage
* E2E scripts

Still future work:

* stricter timestamp consistency validation
* calibration / ego-pose validity checks
* official nuScenes metrics
* model comparison API
* detection leaderboard
* pipeline/job timeline API
* Prometheus/Grafana observability
* MinIO/S3 artifact backend
* Triton/external serving backend
* simulation/counterfactual dataset contract

---

## Next priorities

1. **Model Comparison API**

   * compare evaluation runs by dataset version
   * compare model versions by precision, recall, and center-distance metrics

2. **Detection Leaderboard**

   * rank model versions by selected metric
   * expose evaluation history for each dataset/model pair

3. **Operational Timeline**

   * pipeline step timeline
   * job event timeline
   * queue/start/finish duration fields

4. **Dataset Quality Hardening**

   * timestamp consistency checks
   * calibration / ego-pose validation
   * profile report regression tests

5. **Artifact Backend Extension**

   * MinIO / S3-compatible artifact storage
   * artifact metadata table
   * artifact checksum / size metadata

6. **Simulation Extension Contract**

   * register reconstructed/simulated/counterfactual dataset versions
   * connect real2sim2real outputs to validation, profiling, and evaluation workflows

---

## Positioning

SceneOps demonstrates:

* robotics sensor data pipeline design
* dataset validation and profiling infrastructure
* AI/MLOps workflow orchestration
* metadata and artifact management
* model inference/evaluation lifecycle design
* asynchronous execution architecture
* scalable backend system design
* production-oriented AI system thinking
