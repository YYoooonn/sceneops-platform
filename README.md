# SceneOps Platform

SceneOps Platform is a local-first robotics data and MLOps platform for scene-centric dataset management, dataset quality evaluation, model inference/evaluation, and scenario curation.

It uses nuScenes mini as a realistic autonomous-driving dataset and implements production-like components such as FastAPI control plane, PostgreSQL metadata store, object-storage-style artifact management, Celery-based async execution, and pipeline/job orchestration.

> Modern robotics AI systems require more than model inference. They need reliable sensor data ingestion, scene-level quality control, reproducible evaluation, artifact lineage, and data selection workflows for model improvement.
>
> SceneOps explores this problem as a small but production-shaped platform.

---

## Implementation

SceneOps Platform currently implements a local-first, production-shaped data and MLOps workflow for scene-centric robotics datasets.


| Area                       | Status         | Description                                    |
| -------------------------- | -------------- | ---------------------------------------------- |
| API control plane          | ✅              | FastAPI control plane                          |
| Metadata store             | ✅              | PostgreSQL + Alembic                           |
| Artifact storage           | ✅              | Local/S3-compatible artifact URIs              |
| Async execution            | ✅              | Celery + Redis workers                         |
| Dataset registry           | ✅              | Dataset/version metadata                       |
| Scene registry             | ✅              | Canonical `SceneRecord` catalog                |
| Dataset manifest           | ✅              | DB-backed derived manifest                     |
| Scene validation/profile   | ✅              | Per-scene quality runs                         |
| Dataset quality            | ✅              | Scene-quality aggregate                        |
| Scene quality APIs         | ✅              | Scene and dataset-version quality views        |
| Mock detection             | ✅              | Fast contract-test backend                     |
| Real detection             | ✅              | GroundingDINO inference backend                |
| Detection evaluation       | ✅              | Metrics, artifacts, leaderboard                |
| Detection comparison       | ✅              | Quality → selection → evaluation debug view    |
| Scenario curation          | ✅ Experimental | Scene mining + readiness scoring               |
| Scenario records/artifacts | ✅              | ScenarioSet + scenario run records             |
| E2E scripts                | ✅              | Dataset, detection, comparison, curation flows |
| Operations views           | ✅              | Summary, timeline, failures                    |
| Leaderboards               | ✅              | Evaluation/model/dataset rankings              |

The current platform demonstrates three end-to-end workflows:

1. **Scene-first dataset quality**
  Scene-level validation/profile/GT signals are aggregated into dataset readiness.
2. **Real detection evaluation**
  GroundingDINO predictions are stored, evaluated, and recorded in the leaderboard.
3. **Scenario curation**
  Scene quality signals are converted into scenario candidates and readiness scores.

---

## Core concepts

SceneOps is designed around a scene-first data model.

```text
SceneRecord = canonical source
DatasetManifest = derived artifact
DatasetQuality = aggregate view
ScenarioSet = curated selection artifact
```

### Scene

A `Scene` is the canonical unit of registered sensor data.

`SceneRecord` stores scene membership and metadata such as sample count, frame count, annotation count, GT availability, sensor channels, status, and artifact references.

### DatasetManifest

A `DatasetManifest` is a derived snapshot generated from registered `SceneRecord` rows.

It is used by pipelines and evaluation jobs, but it is not the source of truth for scene membership.

### Scene Quality

Scene quality describes whether a scene is usable for downstream workflows.

It combines validation results, profile results, GT availability, annotation count, sensor coverage, and exclusion reasons.

### Dataset Quality

Dataset quality is an aggregate view over scene quality.

It summarizes readiness, selectable scenes, excluded scenes, GT coverage, observed channels, and dataset-level counts.

### Detection Evaluation

Detection evaluation runs model predictions against selectable scenes.

Scene quality determines which scenes are evaluated and which scenes are skipped, making evaluation results explainable.

### Scenario

A `Scenario` is a purpose-specific, derived view of a scene or a group of scenes.

It is not a new raw data unit. It represents a curated candidate for downstream use cases such as evaluation, reconstruction, pseudo-labeling, or model debugging.

### ScenarioSet

A `ScenarioSet` is an artifact-backed curation result.

It groups mined scenario candidates and stores readiness scores, selection reasons, and report artifacts generated by the scenario curation pipeline.

---

## Architecture

```text
Client
  └─ FastAPI API ───────── Postgres
       │                   metadata, run state, artifact URIs
       │
       └─ Redis / Celery ─► Pipeline Worker ─► Postgres
                         └► Job Worker      ─► Artifact Store
                                               MinIO / local
```


| Layer            | Package                     | Role                                                                                                      |
| ---------------- | --------------------------- | --------------------------------------------------------------------------------------------------------- |
| Control plane    | `apps/api`                  | REST API for datasets, scenes, scenarios, jobs, pipelines, runs, artifacts, evaluations, and leaderboards |
| Data plane       | `apps/worker`               | Pipeline orchestration, job execution, and artifact writes                                                |
| Metadata store   | `packages/sceneops-db`      | PostgreSQL metadata, run state, repositories, and Alembic migrations                                      |
| Artifact store   | `packages/sceneops-storage` | Local and S3-compatible artifact storage                                                                  |
| Domain contracts | `packages/sceneops-core`    | Pydantic schemas, enums, job contracts, and pipeline definitions                                          |
| Inference server | `apps/inference-server`     | Optional GroundingDINO inference server                                                                   |


### Execution model

SceneOps separates workflow orchestration from job execution.

```text
Pipeline
  └─ PipelineTask
      └─ Job
          └─ Domain run / artifact
```


| Unit            | Meaning                                                                        |
| --------------- | ------------------------------------------------------------------------------ |
| Pipeline        | Reusable workflow definition                                                   |
| PipelineRun     | One execution of a pipeline                                                    |
| Task            | Ordered stage inside a pipeline                                                |
| PipelineTaskRun | Runtime state of a task                                                        |
| Job             | Executable unit dispatched to a worker                                         |
| Domain run      | Result record such as validation, inference, evaluation, or scenario readiness |


#### Implemented pipelines

`dataset_scene_ingestion`
  ingest_scenes → register_scene → validate_scene → profile_scene → build_scene_index → build_dataset_manifest

`raw_log_scene_building`
  build_scenes → register_scene → validate_scene → profile_scene → build_scene_index → build_dataset_manifest

`detection_evaluation`
  predict_detection → evaluate_detection

`scenario_curation`
  mine_scenarios → score_scenario_readiness

#### Planned pipeline definitions

`scene_reconstruction`
  build_scenes → validate_scene → profile_scene → export_scene_package

`scene_registration`
  register_scene → validate_scene → profile_scene → compare_scenes

`generated_dataset_preparation`
  register_scene → compare_scenes → auto_label_scene → build_dataset_manifest → check_distribution → export_dataset

### Pipeline result buckets

Task results are normalized before being stored in the final pipeline result.

```text
outputs           ← downstream refs
metrics           ← numeric counts and scores
lineage.artifacts ← artifact URIs
tasks[].summary   ← step-level summary
tasks[].rawResult ← debug detail
```

> Pipeline runner stays generic.
> Job-specific fields are mapped by each task output spec,
> which keeps the execution model compatible with external orchestrators such as Airflow.

---

## Demo 1: scene-first quality → detection evaluation

SceneOps treats `SceneRecord` as the canonical source.
Dataset quality is an aggregate over scene-level signals, not a single dataset-level run.
Detection evaluation reads the same scene-level view to show which scenes were selected, evaluated, or skipped.

### Quickstart

```bash
make e2e-dataset-ingestion        # dataset_scene_ingestion pipeline (10 nuScenes GT scenes)
make e2e-raw-log-scene-building   # raw_log_scene_building pipeline (20 non-GT scenes)
make inference-local-up
make e2e-detection-evaluation-real     # grounding-dino inference server detection evaluation
make compare-detection PIPELINE_RUN_ID=<id>
```

### Captured output - 30-scene dataset: 10 GT(nuscenes) + 20 non-GT(raw-log-style scenes)

```
=== Dataset Quality ===
  readiness                     : warning
  scene_count                   : 30
  sample_count                  : 808
  frame_count                   : 1616
  annotation_count              : 18538
  ready/warning/blocked/unknown : 30 / 0 / 0 / 0
  selectable_for_detection      : 10
  non_selectable_for_detection  : 20
  ground_truth_scenes           : 10
  gt_coverage_ratio             : 0.3333
  observed_channels             : CAM_FRONT, LIDAR_TOP
  exclusion_reasons             : {"missing_ground_truth":20}

=== Detection Run Comparison ===
  selected_scene_count  : 10
  selected_sample_count : 404
  skipped_scene_count   : 20
  evaluated_scene_count : 10
  eval_skipped_count    : 0
  ground_truth_count    : 14982
  prediction_count      : 2340
  evaluable_pred_count  : 2340
  primary_metric        : precision = 0.318803
```

**Consistency check:**

```
selectable_for_detection = 10  →  selected_scene_count = 10
non_selectable = 20            →  skipped_scene_count  = 20
missing_ground_truth = 20      →  20 scenes skipped with scene_has_no_ground_truth
selected scenes = 10           →  evaluated scenes     = 10
```

`readiness=warning` is expected when only 10/30 scenes are selectable for detection.

`annotation_count` (18538) is the dataset-level count from `SceneRecord`;
`ground_truth_count` (14982) is the evaluator-side count after evaluation-specific loading and filtering
— these values can differ.

---

## Demo 2: real GroundingDINO detection evaluation

SceneOps supports both a fast mock backend and a real GroundingDINO backend.

```bash
make local-up
make inference-local-up   # or make inference-gpu-up for GPU
make e2e-dataset-ingestion
make e2e-detection-evaluation-real
```

**Validated flow:**

```
GroundingDINO server readiness (/healthz + /readyz)
→ predict_detection  backend=grounding_dino
→ prediction manifest + prediction shards written to artifact store
→ evaluate_detection  evaluator=center-distance, match_distance_m=2.0
→ metrics artifact
→ leaderboard entry
```

**Mock backend (10 nuScenes GT scenes only):**

```
prediction_count:   12544
ground_truth_count: 14982
primary_metric:     precision ≈ 0.991948  (mock detector)
```

**GroundingDINO backend (10 nuScenes GT scenes, CAM_FRONT only):**

```
prediction_count:   2340
ground_truth_count: 14982
primary_metric:     precision ≈ 0.318803
```

This is a real-model E2E smoke result on a limited nuScenes mini sample set, not a production benchmark.

---

## Demo 3: scenario curation

Scenario curation converts scene-level quality signals into a data-selection workflow.

> mines candidate scenes from `SceneRecord` metadata
> → stores them as a `ScenarioSet` artifact
> → scores their readiness for downstream evaluation or reconstruction workflows
>
> No image or lidar data is loaded.

```bash
make e2e-dataset-ingestion
make e2e-scenario-curation
```

**Pipeline result shape:**

```json
{
  "outputs": {
    "scenario_set_id": "scset-...",
    "scenario_set_uri": "s3://.../candidates.json",
    "mining_run_id": "mining-...",
    "readiness_run_id": "readiness-..."
  },
  "metrics": {
    "candidate_count": 10,
    "selected_count": 10,
    "rejected_count": 20,
    "scored_scene_count": 10,
    "ready_count": 10,
    "warning_count": 0,
    "blocked_count": 0,
    "average_score": 0.9092
  },
  "lineage": {
    "artifacts": {
      "mining_report_uri": "s3://.../report.json",
      "readiness_report_uri": "s3://.../report.json"
    }
  }
}
```

**Current limitations (artifact-backed first phase):**

- No per-scenario item table — candidates are stored in a JSON artifact
- `selectable_for_detection` is derived from `SceneRecord` signals; full selectability from detection comparison runs is a future enhancement
- Future: evaluation-aware mining (FP/FN by scene), pseudo-label candidate scoring, VLM semantic tags

---
## API overview

All API routes are under `/api/v1`. Health is exposed at the root.

SceneOps exposes APIs across four main surfaces:

| Surface          | Areas                                       | Purpose                                                      |
| ---------------- | ------------------------------------------- | ------------------------------------------------------------ |
| Data catalog     | Datasets, Scenes, Scenarios, Models, Labels | Register and inspect data/model resources                    |
| Execution        | Pipelines, Jobs, Executions                 | Create, run, and monitor asynchronous workflows              |
| Runs & artifacts | Inference, Evaluations, Artifacts           | Track model runs, metrics, outputs, and artifact lineage     |
| Operations       | Operations, Leaderboards                    | Operator-facing summaries, failures, timelines, and rankings |

Representative routes:

```text
GET  /health

# Data catalog
GET  /api/v1/datasets
GET  /api/v1/datasets/{id}/versions/{v}/quality
GET  /api/v1/scenes/{scene_id}/quality
GET  /api/v1/scenarios/{scenario_set_id}/artifacts
GET  /api/v1/models/{model_id}/versions/{v}

# Execution
POST /api/v1/pipelines/runs
POST /api/v1/pipelines/runs/{id}/execute
GET  /api/v1/jobs/{job_id}/events
GET  /api/v1/executions/{execution_id}

# Runs and artifacts
GET  /api/v1/inference/runs/{id}/artifacts
GET  /api/v1/evaluations/runs/{id}/metrics
GET  /api/v1/artifacts/{artifact_id}

# Operations
GET  /api/v1/operations/summary
GET  /api/v1/operations/failures
GET  /api/v1/leaderboards/evaluations
```

Explore all registered routes:

```bash
curl http://localhost:8000/openapi.json | jq '.paths | keys[]'
```

---

## Quickstart

**Requirements:** Docker + Docker Compose, [uv](https://github.com/astral-sh/uv), Python 3.11–3.12, nuScenes mini at `./data/raw/nuscenes`.

```bash
cp .env.example .env.local          # configure storage backend, DB, Redis
make setup                          # install deps + pre-commit hooks
make local-up                       # MinIO + Postgres + Redis + API + workers
make register-nuscenes-dataset      # register nuScenes fixture
make e2e                            # all E2E tests (mock backend)
```

**Artifact storage backend** (`.env.local`):

```bash
# local filesystem
SCENEOPS_WORKER_ARTIFACT__BACKEND=local

# MinIO / S3
SCENEOPS_WORKER_ARTIFACT__BACKEND=minio
SCENEOPS_WORKER_ARTIFACT__ROOT_URI=s3://sceneops
SCENEOPS_WORKER_ARTIFACT__ENDPOINT_URL=http://minio:9000
```

---

## Common commands

### Stack


| Command            | Description                                           |
| ------------------ | ----------------------------------------------------- |
| `make local-up`    | Start full stack (MinIO + DB + Redis + API + workers) |
| `make local-down`  | Stop all services                                     |
| `make local-reset` | Wipe volumes and restart                              |
| `make db-migrate`  | Run Alembic upgrade head                              |


### Development


| Command                     | Description                   |
| --------------------------- | ----------------------------- |
| `make test`                 | Run worker unit tests         |
| `make lint` / `make format` | Ruff check / format           |
| `make worker-imports`       | Validate job registry imports |


### E2E


| Command                                       | Description                                |
| --------------------------------------------- | ------------------------------------------ |
| `make e2e`                                    | All E2E tests (mock backend)               |
| `make e2e-api-smoke`                          | API smoke                                  |
| `make e2e-dataset-ingestion`                  | Ingestion pipeline                         |
| `make e2e-raw-log-scene-building`             | Raw log scene building                     |
| `make e2e-detection-evaluation`               | Detection evaluation (mock)                |
| `make e2e-detection-evaluation-real`          | Detection evaluation (real GroundingDINO)  |
| `make e2e-pipeline-contracts`                 | Pipeline contract validation               |
| `make e2e-scenario-curation`                  | Scenario curation pipeline                 |
| `make compare-detection PIPELINE_RUN_ID=<id>` | Dataset quality + detection run comparison |


---

## Repository structure

```
sceneops-platform/
├── apps/
│   ├── api/                        # FastAPI control plane
│   │   └── app/
│   │       ├── platform/           # jobs, pipelines, executions, artifacts
│   │       ├── domains/            # datasets, scenes, models, scenarios, inference, evaluations
│   │       └── views/              # operations, leaderboards
│   ├── inference-server/           # GroundingDINO server (FastAPI, port 8001; optional)
│   └── worker/
│       └── sceneops_worker/
│           ├── pipelines/          # PipelineRunner, TaskRunner, InputResolver, Planner,
│           │                       #   ResultBuilder, ResultRecorder, QualityGate
│           ├── jobs/               # handlers: dataset/, evaluation/, inference/, scenarios/
│           ├── scenes/             # validator, profiler, raw scene builder, selection filter
│           ├── evaluation/detection/  # CenterDistanceDetectionEvaluator, accumulator
│           ├── inference/          # mock / ONNX / GroundingDINO + frustum-lift backends
│           ├── core/               # WorkerContext, stores, DI
│           ├── execution/          # Celery app factory, job dispatcher
│           └── tests/              # unit tests
├── packages/
│   ├── sceneops-core/              # domain schemas, enums, pipeline definitions
│   ├── sceneops-db/                # SQLAlchemy models, async repositories, Alembic
│   └── sceneops-storage/           # LocalArtifactStore, S3ArtifactStore
├── migrations/                     # Alembic versions
├── scripts/
│   ├── e2e/                        # E2E scripts
│   ├── fixtures/                   # dataset registration
│   └── debug/                      # pipeline/job inspection
├── docker-compose.local.yml
├── Makefile
└── pyproject.toml                  # uv workspace (Python 3.11–3.12)
```

---

## Limitations and roadmap

### Current limitations

* The default local dataset is nuScenes mini.
* The platform is local-first and optimized for architecture validation, not large-scale production throughput.
* GroundingDINO evaluation results are integration signals, not production model benchmarks.
* Scenario curation is implemented but still marked `experimental=True`.
* Scenario candidates are artifact-backed; there is no per-scenario item table yet.
* Scenario readiness scoring currently uses metadata and scene-quality signals, not image/LiDAR content.
* Scene reconstruction, auto-labeling, and generated dataset preparation pipelines are defined but not implemented.
* Operations and leaderboard APIs exist, but there is no dedicated web UI yet.

### Roadmap

* Airflow DAG integration using `run-pipeline-task` as an external task entry point.
* Evaluation-aware scenario mining using FP/FN and per-scene metric signals.
* Pseudo-label candidate workflow for no-GT or weakly labeled scenes.
* VLM-based semantic scene tagging.
* Scene reconstruction package export.
* Auto-labeling pipeline with a labeler registry.
* Per-scenario item table for queryable scenario candidates and review status.
* Web UI on top of existing operations and leaderboard APIs.
* Cloud object storage hardening, including stronger artifact lifecycle and integrity checks.

---

## Tech stack


|                      |                                                       |
| -------------------- | ----------------------------------------------------- |
| API                  | FastAPI, Pydantic v2, Uvicorn                         |
| Task queue           | Celery, Redis 7                                       |
| Database             | PostgreSQL 16, SQLAlchemy 2.0 async, Alembic          |
| Artifact storage     | MinIO (S3 API), boto3                                 |
| Inference (optional) | GroundingDINO, HuggingFace Transformers, ONNX Runtime |
| Scene data           | nuScenes DevKit                                       |
| Package manager      | uv workspace                                          |
| Code quality         | Ruff, pre-commit                                      |
| Infra                | Docker Compose                                        |
