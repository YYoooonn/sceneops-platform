# SceneOps Platform

**SceneOps Platform** is a local-first MLOps control plane for robotics perception workflows.

It connects robotics sensor datasets, dataset validation, model inference, detection evaluation, artifact lineage, and asynchronous execution into one reproducible pipeline system.

```text
robotics sensor data
  -> dataset registry
  -> ingestion + manifest generation
  -> dataset validation / quality gate
  -> model inference
  -> detection evaluation
  -> artifact lineage
  -> model/runtime iteration
```

The current implementation uses **FastAPI**, **PostgreSQL**, **Redis/Celery**, local artifact storage, and pluggable inference backends including **mock** and **ONNX Runtime**.

---

## Why this project exists

Robotics AI systems are not only model-training problems. A practical robotics AI workflow needs repeatable infrastructure for:

- sensor dataset ingestion and preprocessing
- dataset versioning and quality validation
- model version management
- batch inference execution
- evaluation and metric tracking
- artifact lineage across datasets, predictions, and evaluations
- asynchronous job/pipeline orchestration
- operational visibility for failed or blocked runs

SceneOps is a portfolio-scale implementation of that system, focused on nuScenes-style autonomous-driving sensor data and 3D perception pipeline infrastructure.

---

## What is implemented now

| Area | Current implementation |
|---|---|
| Control plane | FastAPI API server with dataset, model, job, pipeline, run, and artifact routes |
| Metadata store | PostgreSQL-backed repositories with Alembic migrations |
| Async execution | Redis broker + Celery worker runtime |
| Dataset registry | Dataset and dataset version registration with source/manifest metadata |
| Model registry | Model and model version registration with backend/model URI metadata |
| Pipelines | `dataset_ingestion` and `detection_validation` pipeline definitions |
| Job runtime | `ingest_dataset`, `validate_dataset_manifest`, `predict_detection`, `evaluate_detection` jobs |
| Dataset ingestion | nuScenes mini ingestion into dataset manifests and sample manifests |
| Dataset validation | Manifest existence, scene/sample availability, required sensor channel validation |
| Quality gate | Dataset version is promoted to `ready` only after validation succeeds |
| Inference | Mock detection backend and ONNX Runtime dummy detector backend |
| Evaluation | Center-distance detection evaluator with TP/FP/FN, precision, recall, mean center distance error, and per-class metrics |
| Artifact lineage | Dataset manifests, prediction manifests, evaluation manifests, and run records |
| E2E scripts | Dataset ingestion, mock detection validation, and ONNX detection validation E2E scripts |

---

## Current E2E workflows

### 1. Dataset ingestion + validation

```text
create dataset_ingestion pipeline run
  -> dispatch pipeline through Celery
  -> ingest nuScenes dataset version
  -> write dataset / scene / sample manifests
  -> validate scene and sample manifests
  -> validate required sensor channels, e.g. CAM_FRONT and LIDAR_TOP
  -> mark dataset version ready when validation succeeds
```

Run:

```bash
make e2e-dataset-ingest
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
  - prediction
  - evaluation
              |
              v
[Artifact Store]
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
  migrations/             # Alembic migrations
  scripts/
    checks/               # local health/import/broker checks
    debug/                # run/job inspection scripts
    e2e/                  # end-to-end validation scripts
    fixtures/             # dataset/model registration fixtures
  data/
    raw/                  # local raw dataset mount
    datasets/             # generated dataset manifests
    runs/                 # inference/evaluation outputs
    models/               # local model artifacts
  docs/                   # architecture, E2E, contracts, operations, roadmap
  docker-compose.local.yml
  Makefile
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

---

## Final product goal

The final goal is to build a production-like MLOps platform for robotics perception workflows that can:

1. ingest robotics sensor datasets,
2. validate and profile dataset quality,
3. run model inference through multiple runtime backends,
4. evaluate predictions reproducibly,
5. compare model versions,
6. track artifact lineage,
7. monitor asynchronous pipeline execution,
8. and later connect simulated/counterfactual datasets into the same model-evaluation loop.

---

## Near-term support sprint

Before using this project as a robotics AI portfolio project, the highest-impact next steps are:

1. **Dataset Profile Report**
   - class distribution
   - sensor completeness ratio
   - sample statistics
   - timestamp / calibration fields as future schema targets

2. **Stronger Dataset Quality Gate**
   - explicit validation report artifact
   - validation failure reason summary
   - blocked pipeline status explanation

3. **Model Comparison API**
   - compare evaluation runs by dataset version
   - compare model versions by precision/recall/center-distance metrics

4. **Detection Leaderboard**
   - simple leaderboard for detection validation runs
   - sorted by chosen metric

5. **Operational Timeline**
   - pipeline step timeline
   - job event timeline
   - queue/start/finish duration fields

6. **Simulation Extension Contract**
   - register reconstructed/simulated/counterfactual dataset versions
   - connect real2sim2real outputs to validation and evaluation workflows

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [E2E Workflows](docs/E2E.md)
- [Dataset and Artifact Contracts](docs/DATASET_AND_ARTIFACTS.md)
- [Model and Evaluation Contracts](docs/MODEL_AND_EVALUATION.md)
- [Operations](docs/OPERATIONS.md)
- [Simulation Extension](docs/SIMULATION_EXTENSION.md)
- [Roadmap](docs/ROADMAP.md)

---

## Positioning

SceneOps demonstrates:

- robotics sensor data pipeline design
- AI/MLOps workflow orchestration
- metadata and artifact management
- model inference/evaluation lifecycle design
- asynchronous execution architecture
- scalable backend system design
- production-oriented AI system thinking
