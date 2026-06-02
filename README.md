# SceneOps Platform

**Robotics Sensor Data & Model Validation Platform**

SceneOps is a local-first, production-oriented platform for managing robotics
sensor datasets, validating dataset quality, profiling sensor coverage, running
model inference, evaluating detection results, and tracking artifacts across the
full data-to-model workflow.

The premise is that robotics AI systems need more than model training. They need
repeatable dataset ingestion, sensor-data validation, dataset profiling,
model-versioned inference, evaluation, artifact lineage, and operational
visibility. SceneOps provides a control plane and execution runtime for that
workflow.

The current focus is autonomous-driving-style multi-sensor data using
**nuScenes mini**, but the architecture is built to extend to other robotics
datasets, inference backends, object storage, and simulation-generated data.

---

## Workflow

```text
raw sensor data
  -> dataset registration
  -> ingestion
  -> validation
  -> profiling
  -> model inference
  -> evaluation
  -> artifact lineage
  -> comparison / leaderboard
```

The goal is not to run a model once, but to answer operational questions:

```text
Is this dataset version valid enough for downstream model workflows?
What sensors, scenes, samples, and annotations does this dataset contain?
Which model version was evaluated on which dataset version?
Where are the prediction and evaluation artifacts stored?
Which pipeline step failed, and why?
Can the same workflow later run on S3/MinIO, Triton, or simulation data?
```

---

## Tech stack

| Layer            | Implementation                                                        |
| ---------------- | --------------------------------------------------------------------- |
| Control plane    | FastAPI (`apps/api`)                                                   |
| Execution        | Celery on Redis, split into `worker-pipeline` and `worker-jobs`       |
| Metadata store   | PostgreSQL with Alembic migrations (`packages/sceneops-db`)           |
| Domain layer     | Shared schemas/contracts (`packages/sceneops-core`)                  |
| Artifact storage | Pluggable `ArtifactStore`; local filesystem implemented (`packages/sceneops-storage`) |
| Inference        | Pluggable backends: `mock` and `onnx_runtime`                         |
| Tooling          | `uv` workspace, Docker Compose, pre-commit, Ruff                      |

Repository layout:

```text
apps/
  api/      FastAPI control plane
  worker/   Celery pipeline + job runtime
  web/      dashboard (planned, not yet implemented)
packages/
  sceneops-core/     domain schemas, contracts, constants
  sceneops-db/       PostgreSQL models and repositories
  sceneops-storage/  artifact store implementations
migrations/          Alembic migrations
scripts/             checks, e2e, debug, fixtures
```

---

## What is implemented today

| Area                  | Implementation                                                                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| Control plane         | FastAPI with dataset, model, job, pipeline, run, artifact, evaluation, leaderboard, and operations routers      |
| Metadata store        | PostgreSQL repositories with Alembic migrations                                                                 |
| Async execution       | Redis broker + Celery, with separate `worker-pipeline` and `worker-jobs`                                        |
| Pipeline runtime      | `PipelineRunner`, `PipelineStepExecutor`, `PipelineResultPropagator`, `PipelineQualityGate`                     |
| Job runtime           | `JobRunner` + `JobHandlerRegistry` with typed handlers                                                          |
| Runtime store registry| `RuntimeStoreRegistry` assembles shared worker dependencies in one place                                        |
| Dataset registry      | `Dataset` / `DatasetVersion` registration with source and manifest metadata                                     |
| Dataset ingestion     | nuScenes mini ingestion into dataset, scene, and sample manifests                                               |
| Dataset validation    | `validate_dataset` job, validation run record, validation report artifact, and quality gate                     |
| Dataset profiling     | `profile_dataset` job, profile run record, sensor coverage, observed channels, annotation statistics            |
| Dataset quality cache | Latest validation/profile summary cached on `DatasetVersion`                                                    |
| Model registry        | `Model` / `ModelVersion` registration with backend and model-URI metadata                                       |
| Pipelines             | `dataset_ingestion` (ingest → validate → profile) and `detection_validation` (predict → evaluate)               |
| Inference             | Mock detection backend and ONNX Runtime dummy detector backend                                                  |
| Evaluation            | Center-distance detection evaluator: TP/FP/FN, precision, recall, mean center distance error, per-class metrics |
| Comparison            | Dataset-version evaluation comparison and per-model-version evaluation history APIs                             |
| Leaderboard           | Metric-direction-aware detection leaderboard API                                                                |
| Operations visibility | Job timeline, pipeline timeline, and status summary with queue latency, heartbeat, and recent failures          |
| Artifact lineage      | Dataset manifests, validation/profile reports, prediction/evaluation manifests, and run records                 |
| E2E scripts           | Ingestion, mock-detection, and ONNX-detection end-to-end pipelines over Celery                                  |

Job types (`JobType`): `ingest_dataset`, `validate_dataset`, `profile_dataset`,
`predict_detection`, `evaluate_detection`.

---

## Execution architecture

SceneOps separates the control plane, the orchestration worker, and the job
execution worker.

```text
[FastAPI Control Plane]
  create pipeline runs / jobs, dispatch executions, expose read APIs
        |
        v
[Redis Broker]
  queues: sceneops.pipeline_runs, sceneops.jobs
        |
        +--> [worker-pipeline] (queue: sceneops.pipeline_runs)
        |        PipelineRunner -> PipelineStepExecutor
        |        creates step jobs, propagates results, applies quality gates
        |
        +--> [worker-jobs] (queue: sceneops.jobs)
                 JobRunner -> JobHandlerRegistry -> typed handler
                 ingest / validate / profile / predict / evaluate
                 persists job result and job events
```

Separation of concerns:

```text
Pipeline worker = orchestration
Job worker      = execution
PostgreSQL      = source of truth (job/pipeline status, results, timeline)
Redis/Celery    = execution transport
Artifact Store  = large output storage
```

PostgreSQL is the source of truth; the pipeline runtime currently waits on job
state through bounded DB status polling. This keeps the lifecycle boundary open
for external orchestrators (Airflow / Argo / Temporal) later.

### Runtime store registry

```text
RuntimeStoreRegistry
  -> artifact_store / dataset_artifact_store / run_artifact_store
  -> job_store / job_event_store / pipeline_store
  -> dataset_registry_store / model_registry_store / run_registry_store
```

`PipelineRunner` and `JobRunner` share the same dependency graph through the
registry, so concrete store construction lives in one place.

---

## API surface

All routers are mounted under `/api/v1` (plus `/health`).

```text
Datasets      /api/v1/datasets
Models        /api/v1/models
Pipelines     /api/v1/pipelines
Jobs          /api/v1/jobs
Runs          /api/v1/runs
Artifacts     /api/v1/artifacts
Evaluations   /api/v1/evaluations
Leaderboards  /api/v1/leaderboards
Operations    /api/v1/operations
```

### Operational visibility

```text
GET /api/v1/operations/jobs/{job_id}/timeline
GET /api/v1/operations/pipelines/{pipeline_run_id}/timeline
GET /api/v1/operations/summary
```

These expose job status and events, step-level status, queue latency, execution
duration, worker heartbeat, and recent failures, so pipeline runs are debuggable
without reading container logs or querying the database by hand.

### Evaluation comparison and leaderboard

```text
GET /api/v1/evaluations/compare?dataset_id=...&dataset_version=...
GET /api/v1/evaluations/models/{model_id}/versions/{model_version}
GET /api/v1/leaderboards/detection?dataset_id=...&dataset_version=...&sort_by=precision
```

Supported detection metrics: `precision`, `recall`, `mean_center_distance_error`,
`sample_count`, and per-class metrics. The leaderboard is metric-direction aware:

```text
precision                  -> higher is better
recall                     -> higher is better
mean_center_distance_error -> lower is better
```

The same `detection_validation` pipeline can run for multiple model versions and
be compared under a single dataset version.

---

## Quickstart

### 1. Prepare local directories

```bash
make prepare-data
```

Expected directories: `data/raw/`, `data/datasets/`, `data/runs/`, `data/models/`.
Place nuScenes mini under the configured raw path, e.g. `data/raw/nuscenes/`.

### 2. Build and start services

```bash
make compose-build
make compose-up
```

Starts PostgreSQL, Redis, the FastAPI API server, `worker-pipeline`
(queue `sceneops.pipeline_runs`), and `worker-jobs` (queue `sceneops.jobs`).

### 3. Run migrations and check the environment

```bash
make db-migrate
make check-env
make check-imports
make check-celery
```

### 4. Register the nuScenes dataset fixture

```bash
make register-nuscenes-dataset
```

### 5. Run end-to-end workflows

```bash
make e2e-dataset-ingest
make e2e-mock-celery
make e2e-onnx-celery
```

### 6. Inspect results

```bash
make show-runs
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
make show-job-events JOB_ID=job-xxx
```

```bash
curl "http://localhost:8000/api/v1/operations/summary" | jq
curl "http://localhost:8000/api/v1/evaluations/compare?dataset_id=nuscenes&dataset_version=v1.0-mini" | jq
curl "http://localhost:8000/api/v1/leaderboards/detection?dataset_id=nuscenes&dataset_version=v1.0-mini&sort_by=precision" | jq
```

---

## Next milestone — one-week sprint target

The next milestone turns SceneOps from a local-first validation platform into a
cloud-native robotics data infrastructure that can auto-label sensor data with a
foundation model and expose production-grade observability.

Milestone: **SceneOps v0.5 — cloud-native data infra + foundation-model auto-labeling + observability**

| Day   | Deliverable                                                                                                                                                              | Competency                                                          |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| 1     | S3/MinIO object-storage backend behind the existing `ArtifactStore` contract; MinIO in Docker Compose; pipelines run against object storage with `s3://` artifact URIs   | data engineering / infra, cloud data pipelines, S3 storage          |
| 2-3   | `auto_label_dataset` job + `auto_label` pipeline: a pluggable VLM backend (Claude) labels nuScenes camera samples, writes an auto-label run + manifest, scored against GT | foundation-model (VLM) robotics application, auto-labeling, quality |
| 4     | Prometheus `/metrics`, worker heartbeat and queue-depth in the Operations API, Grafana starter dashboards                                                               | scalable system design, monitoring & incident response             |
| 5     | (stretch) LiDAR point-cloud profiling: read `LIDAR_TOP` sweeps for point count / range / density / height-intensity distribution, add 3D coverage to the profile report | 3D sensor data, sensor-fusion-ready profiling                       |
| —     | (stretch) `apps/web` dashboard: dataset quality, detection leaderboard, pipeline timeline, and auto-label review views                                                  | data analysis & visualization                                       |

> Note: `ArtifactBackend` already defines `local`, `minio`, `s3`, and `gcs`, and
> `ArtifactSettings` already carries `bucket` / `endpoint_url` / `region` /
> credentials. Only `LocalArtifactStore` is implemented today — the object-storage
> factory branch still raises `NotImplementedError`, so day 1 is a focused
> store implementation rather than a config redesign.

Definition of done for the core (days 1-4):

```text
object storage:
  - ArtifactBackend.S3 implemented and selectable by config
  - existing ingest/validate/profile/predict/evaluate pipelines run unchanged on MinIO
  - artifact URIs are s3:// and resolve through the same domain logic

auto-labeling:
  - auto_label_dataset job produces an auto_label_run + auto_label_manifest.json
  - VLM backend is pluggable behind an inference-style contract
  - auto-labels are scored against ground truth (precision / recall)

observability:
  - GET /metrics exposes job/pipeline counters, durations, queue depth, worker heartbeat
  - Grafana dashboard renders execution and queue health
  - operations summary includes worker heartbeat and queue depth
```

---

## Roadmap

### Phase 1 — Dataset quality and validation loop · implemented

`validate_dataset` and `profile_dataset` jobs, validation/profile run records,
`validation_report.json` and `profile_report.json` artifacts, the
`DatasetVersion` quality cache, and the validation quality gate.

Next: richer issue-severity presets, per-sensor missing-artifact detail,
configurable validation policy presets, dataset readiness API.

### Phase 2 — Model comparison and leaderboard · implemented

`/evaluations/compare`, `/evaluations/models/{model_id}/versions/{model_version}`,
and `/leaderboards/detection`. Compare runs by dataset version, inspect history
by model version, and rank by metric with direction awareness.

Next: evaluation config hashing, comparison by evaluator configuration,
class-level leaderboard, baseline-vs-candidate diff API.

### Phase 3 — Operational visibility · implemented

Job/pipeline timelines and the operations summary with job events, step status,
duration/latency, and a recent-failure summary.

Next: queue-depth metrics, worker heartbeat summary, Prometheus `/metrics`
endpoint, retry/failure analytics. (Pulled into the next sprint above.)

### Phase 4 — Execution backend hardening · partially implemented

Redis/Celery backend, split `worker-pipeline` / `worker-jobs`, queue routing,
runtime store registry, registry-based `JobRunner`.

Next: extract a `PipelineStepLifecycleService` and a `JobCompletionWaiter`
(polling now, event-driven/outbox later) to keep the boundary open for
Airflow / Argo / Temporal.

### Phase 5 — Artifact storage and lineage hardening · partially implemented

`ArtifactStore` contract with a local implementation and object-storage config
surface already in place. Goal: standardized artifact URI contracts and a
drop-in S3/MinIO store with no change to domain logic.

```text
local://datasets/nuscenes/v1.0-mini/manifest.json
s3://sceneops/datasets/nuscenes/v1.0-mini/manifest.json
s3://sceneops/runs/inference/inf-xxx/predictions.json
```

### Phase 6 — External inference and serving · planned

Add inference backends for external serving (`external_http`, `triton`) behind
the existing `InferenceBackend` contract, keeping evaluation independent of the
serving implementation.

### Phase 7 — Simulation and counterfactual datasets · planned

Dataset source types (`real`, `simulated`, `counterfactual`, `reobserved`) and
lineage fields (`parent_dataset_id`, `parent_dataset_version`,
`generation_run_id`, `transformation_type`).

### Phase 8 — Foundation-model auto-labeling · planned (next sprint)

`auto_label_dataset` job and `auto_label` pipeline (ingest → auto_label →
validate). A VLM backend (Claude) takes a camera sample image plus
sensor/calibration metadata and returns 2D detections (and BEV/3D pseudo-labels
where calibration allows), written as an `auto_label_run` + manifest and scored
against ground truth. This makes auto-labeling a measurable data-quality loop
connected to the existing evaluation and leaderboard read models.

---

## Positioning

SceneOps demonstrates building robotics AI infrastructure from a
data–model–system perspective: sensor-data ingestion, dataset quality validation,
dataset profiling and selection, model-versioned inference, detection evaluation,
async workflow execution, metadata and artifact lineage, and an extensible
backend/storage architecture.

The next milestone extends this along the three axes that matter most for
robotics data engineering: cloud-native object storage (S3/MinIO) for scalable
data pipelines, foundation-model (VLM) auto-labeling as a measurable
data-quality loop, and production observability (Prometheus/Grafana, worker
heartbeat, queue depth) for monitoring and incident response.

The project is a practical foundation for robotics AI workflows where dataset
quality, reproducibility, evaluation, and system reliability matter as much as
model implementation.
