# SceneOps Platform

**SceneOps Platform** is a local-first MLOps platform for robotics perception workflows.

It manages sensor dataset ingestion, dataset validation, model inference, evaluation, artifact lineage, and asynchronous pipeline execution with **FastAPI**, **PostgreSQL**, **Redis/Celery**, and pluggable model/runtime backends.

```text
robotics sensor data
  -> dataset registry
  -> ingestion / validation / profiling
  -> model inference
  -> evaluation
  -> artifact lineage
  -> model/runtime iteration
  -> monitoring / serving
```

## Why this project exists

Robotics AI systems are not only about training a model. They require a repeatable system that connects:

- sensor data collection and preprocessing
- dataset versioning and quality checks
- model version management
- inference execution
- evaluation and metric comparison
- artifact storage and lineage
- asynchronous job/pipeline orchestration
- monitoring and operational visibility

SceneOps Platform is a portfolio-scale implementation of that system. The current focus is **3D perception workflow infrastructure** using nuScenes-style autonomous-driving sensor data.

## Current status

The current implementation includes:

- FastAPI control plane
- PostgreSQL metadata store
- Redis/Celery asynchronous execution
- Worker runtime for jobs and pipelines
- Dataset registry
- Model registry
- Pipeline run and step run tracking
- Job lifecycle and job event tracking
- Inference run tracking
- Evaluation run tracking
- Local artifact storage under `/data`
- Dataset ingestion pipeline E2E script
- Mock detection validation pipeline E2E script
- ONNX Runtime dummy detector pipeline E2E script
- Debug scripts for pipeline/job/run inspection
- Airflow execution dispatcher scaffold for future orchestration backend expansion

## Target architecture

```text
[CLI / E2E Script / Dashboard]
              |
              v
[FastAPI Control Plane]
  - Dataset API
  - Model API
  - Pipeline API
  - Job API
  - Run API
  - Artifact API
  - Execution Dispatch API
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
  - Celery backend
  - Airflow backend scaffold
  - future: Kubernetes / Kubeflow / Argo
              |
              v
[Redis Broker]
              |
              v
[Celery Worker Runtime]
  - PipelineRuntime
  - JobRuntime
  - PipelineRunner
  - JobRunner
              |
              v
[Executors]
  - dataset ingestion
  - dataset validation / profiling
  - prediction
  - evaluation
              |
              v
[Artifact Store]
  - local:// now
  - MinIO / S3 later
              |
              v
[Model Runtime]
  - mock backend
  - ONNX Runtime backend
  - future: Triton backend
```

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
  docs/                   # architecture, roadmap, E2E, contracts
  docker-compose.local.yml
  Makefile
```

## Design principles

```text
API = control plane
Worker = execution plane
Repository = metadata backend abstraction
Storage = artifact backend abstraction
DatasetVersion = versioned sensor data source
ModelVersion = versioned inference runtime artifact
Job = execution command
JobEvent = execution timeline
PipelineRun = workflow execution record
InferenceRun = model execution record
EvaluationRun = metric and validation record
Artifact = output produced by dataset/model/evaluation workflows
```

## Quickstart

### 1. Prepare local data directories

```bash
make prepare-data
```

Expected local directories:

```text
data/raw/
data/datasets/
data/runs/
data/models/
```

Place the nuScenes mini dataset under the configured raw data path, for example:

```text
data/raw/nuscenes/
```

### 2. Build and start local services

```bash
make compose-build
make compose-up
```

This starts PostgreSQL, Redis, FastAPI API server, and the Celery worker.

### 3. Run database migrations

```bash
make db-migrate
```

### 4. Check local environment

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

### 7. Inspect outputs

```bash
make show-runs
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
make show-job-events JOB_ID=job-xxx
```

## Current E2E workflows

### Dataset ingestion

```text
create dataset_ingestion pipeline run
  -> dispatch through Celery
  -> worker executes ingestion
  -> dataset/version metadata is updated
  -> dataset manifest artifact is generated
  -> pipeline status is polled until terminal state
```

### Mock detection validation

```text
register mock detector model
  -> create detection_validation pipeline run
  -> dispatch through Celery
  -> run prediction with mock backend
  -> generate prediction manifest
  -> run center-distance evaluation
  -> store inference/evaluation runs
```

### ONNX Runtime dummy detector validation

```text
create dummy ONNX model artifact
  -> register ONNX model version
  -> create detection_validation pipeline run
  -> dispatch through Celery
  -> run prediction with onnx_runtime backend
  -> evaluate predictions
  -> store inference/evaluation runs
```

## Final product goal

The final product is:

> A production-like MLOps platform for robotics perception workflows that can ingest, validate, profile, run, evaluate, compare, and monitor robotics sensor-data/model pipelines.

The final system should support:

- robotics sensor dataset registry
- dataset versioning
- sensor-centric manifest generation
- data validation and profiling
- model registry
- model versioning
- mock / ONNX / Triton inference backends
- prediction artifact generation
- detection evaluation
- model comparison and leaderboard
- artifact lineage
- local and object-storage artifact backends
- async execution with Redis/Celery
- execution backend abstraction
- monitoring and operational metrics
- dashboard for pipeline, model, and evaluation inspection
- future simulation/counterfactual dataset extension

## Roadmap

### Phase 0. Documentation and repository clarity

- Rewrite README with current status, target architecture, and roadmap
- Add architecture documentation
- Add E2E workflow documentation
- Add dataset/artifact contract documentation
- Add model/evaluation contract documentation
- Add operations documentation
- Align Makefile, docker-compose, scripts, and docs

### Phase 1. Sensor dataset pipeline

- Improve dataset manifest around sensor channels, timestamps, ego pose, calibration, and annotations
- Add dataset validation step
- Add dataset profiling step
- Track missing files, missing channels, invalid samples, class distribution, and sample statistics
- Store validation/profile reports as artifacts

### Phase 2. Model runtime and inference contract

- Stabilize model registry schema
- Strengthen model artifact contract
- Structure inference backends: mock, onnx_runtime, and Triton later
- Separate preprocessing, inference, postprocessing, and prediction export
- Improve ONNX dummy detector into a realistic perception adapter contract

### Phase 3. Evaluation and comparison

- Add model-version evaluation history
- Add model comparison endpoint
- Add detection leaderboard endpoint
- Add per-class metrics
- Add threshold-based metrics
- Add latency and throughput metrics

### Phase 4. Artifact storage and lineage

- Normalize artifact URI contract
- Add `local://` artifact URI convention
- Add artifact metadata table if needed
- Add MinIO/S3-compatible storage backend
- Track dataset, prediction, evaluation, and model artifacts by run lineage

### Phase 5. Observability and operations

- Add pipeline/job duration metrics
- Add queue latency metrics
- Add inference latency metrics
- Add retry/failure metrics
- Add structured logs
- Add Prometheus metrics endpoint
- Add Grafana dashboard

### Phase 6. Serving

- Add Triton inference backend
- Define model repository layout
- Add inference server health checks
- Add API-to-serving request path
- Track serving metrics separately from batch pipeline metrics

### Phase 7. Robotics/simulation extension

- Add simulated/counterfactual dataset type
- Add simulation output manifest contract
- Add camera-LiDAR calibration validation
- Add pseudo-label / auto-label workflow placeholder
- Add VLM/VLA annotation workflow concept

## Positioning

SceneOps Platform demonstrates:

- robotics sensor data pipeline design
- AI/MLOps workflow orchestration
- metadata and artifact management
- model inference/evaluation lifecycle design
- async execution architecture
- scalable backend system design
- production-oriented AI system thinking

It is especially aligned with AI Robotics Engineer and MLOps/Data Infrastructure roles.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [E2E Workflows](docs/E2E.md)
- [Dataset and Artifact Contracts](docs/DATASET_AND_ARTIFACTS.md)
- [Model and Evaluation Contracts](docs/MODEL_AND_EVALUATION.md)
- [Operations](docs/OPERATIONS.md)
