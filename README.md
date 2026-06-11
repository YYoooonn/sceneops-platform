# SceneOps Platform

Local-first MLOps and data platform for robotics and autonomous-driving scene data. Converts raw sensor datasets into structured scene manifests, runs validation and profiling pipelines, and tracks detection inference and evaluation runs through an API control plane and asynchronous worker data plane.

---

## Architecture

```
Client
  └─ FastAPI (API)        ─── Postgres (metadata + artifact URIs)
       │
       └─ Redis / Celery ──► Pipeline Worker  ─► Postgres
                         └─► Job Worker       ─► Artifact Store (MinIO / local)
```

| Layer | Package | Role |
|---|---|---|
| Control plane | `apps/api` | REST API — jobs, pipelines, datasets, evaluations, leaderboards |
| Data plane | `apps/worker` | Celery workers — executes pipelines and jobs, writes artifacts |
| Metadata store | `packages/sceneops-db` | SQLAlchemy 2.0 async ORM, Postgres, Alembic |
| Artifact store | `packages/sceneops-storage` | `LocalArtifactStore` / `S3ArtifactStore` (MinIO-compatible) |
| Domain contracts | `packages/sceneops-core` | Pydantic v2 schemas, enums, pipeline definitions |
| Inference server | `apps/inference-server` | Optional GroundingDINO server (port 8001) |

---

## Validated Pipelines

### 1. `dataset_scene_ingestion`

```
ingest_scenes → register_scene → validate_scene → profile_scene
             → build_scene_index → build_dataset_manifest
```

Validated with nuScenes mini (10 scenes, 404 samples). Produces scene manifests, validation/profile reports, and a dataset version record.

### 2. `raw_log_scene_building`

```
build_scenes → register_scene → validate_scene → profile_scene
            → build_scene_index → build_dataset_manifest
```

Two segmentation/sampling modes:

| Mode | Segmentation | Sampling | When to use |
|---|---|---|---|
| sequence/frame-id | `sequence` — uses `source_sequence_id` hints | `frame_id` — uses `source_frame_id` hints | Source data carries scene/sample IDs (e.g. nuScenes tokens) |
| fixed-window/time-bucket | `fixed_window` — equal-duration time windows | `time_bucket` — timestamp-bucketed samples | No source hints; reconstruct purely from timestamps |

Both modes write a `scene_segment_index` artifact. Params `max_source_sequences` and `max_built_scenes` cap the input and output counts.

### 3. `detection_evaluation`

```
predict_detection → evaluate_detection
```

Validated backends:

| Backend | Purpose | Command |
|---|---|---|
| `mock` | Deterministic pipeline and evaluation-contract validation | `make e2e-detection-evaluation` |
| `grounding_dino` | Real model inference integration path | `make e2e-detection-evaluation-groundingdino` |

**Mock backend — nuScenes mini results:**

```
prediction_count:       12544
ground_truth_count:     14982
primary_metric:         precision ≈ 0.991948  (mock detector, not a real quality benchmark)
evaluator:              center-distance  (2 m match threshold)
```

**GroundingDINO E2E validates:**

```
GroundingDINO server readiness (/healthz + /readyz)
→ predict_detection  backend=grounding_dino, scene_selection=ground_truth_only, CAM_FRONT
→ prediction manifest / prediction shards written to artifact store
→ evaluate_detection  center-distance, match_distance_m=2.0
→ evaluation manifest + metrics artifact
→ leaderboard entry created in DB
```

Prerequisites for the real E2E: `make local-up` + `make inference-local-up` (CPU) or `make inference-gpu-up` (GPU) + `make e2e-dataset-ingestion`.

Current GroundingDINO metrics are integration/evaluation-contract signals, not production-grade 3D detection benchmark results.

The GroundingDINO E2E also fetches and validates prediction/evaluation/metrics artifacts via the API, covering the full path from model-server inference to persisted evaluation artifacts and leaderboard entry.

Artifacts written: prediction manifest, predictions root, evaluation manifest, metrics payload. Leaderboard entry created in DB.

#### Detection run comparison

After either E2E, SceneOps surfaces dataset quality and scene-level selection/evaluation metadata. The comparison script now starts with the compact dataset quality summary, so operators can verify evaluation readiness, GT coverage, selectable scene count, and exclusion reasons before inspecting scene-level prediction/evaluation results.

- **Dataset quality**: readiness, scene/sample/annotation counts, selectable scene count, GT coverage, observed channels, exclusion reasons.
- **Inference run** (`metadata.scene_selection`): selected scene IDs, skipped scenes with reasons, selected sample/annotation counts.
- **Evaluation run** (`summary`): evaluated/skipped scene IDs and counts, GT/prediction/evaluable counts, primary metric.

Inspect a completed run:

```bash
make compare-detection PIPELINE_RUN_ID=<pipeline_run_id>
```

Example output:

```
=== Dataset Quality ===
  dataset                       : nuscenes / v1.0-mini
  readiness                     : ready
  scene_count                   : 10
  sample_count                  : 404
  frame_count                   : 808
  annotation_count              : 14982
  ready/warning/blocked/unknown : 10 / 0 / 0 / 0
  selectable_for_detection      : 10
  non_selectable_for_detection  : 0
  ground_truth_scenes           : 10
  annotated_scenes              : 10
  gt_coverage_ratio             : 1.0
  observed_channels             : CAM_FRONT, LIDAR_TOP
  exclusion_reasons             : {}

=== Detection Run Comparison ===
  inference_run_id  : infer-abc123
  evaluation_run_id : eval-def456

--- Summary ---
  selected_scene_count  : 10
  selected_sample_count : 404
  skipped_scene_count   : 0
  evaluated_scene_count : 10
  ground_truth_count    : 14982
  prediction_count      : 12544
  primary_metric        : precision = 0.991948

--- Scene table ---
scene_id                          selected    evaluated   skip_reason                     samples
scene-001...                      yes         yes         -                                     -
scene-002...                      no          no          scene_has_no_ground_truth            40
```

---

### Scene quality summary

SceneOps exposes scene-level quality because validation and profiling are performed per scene:

```bash
curl http://localhost:8000/api/v1/scenes/<scene_id>/quality
```

The response summarizes scene counts, GT availability, latest scene validation status (`should_block_pipeline`, issue/warning counts), observed channels, and whether the scene is selectable for detection evaluation. `exclusion_reasons` lists why a scene would be skipped by `predict_detection` (e.g. `missing_ground_truth`, `validation_blocked`).

Scene quality uses GT metadata persisted on `SceneRecord` during `register_scene`, while profile runs provide additional measured summaries such as observed channels and asset/coverage details.

Dataset quality is treated as an aggregate view over these scene-level summaries.

### Dataset scene quality

SceneOps can list scene-level quality summaries for a dataset version:

```bash
curl http://localhost:8000/api/v1/datasets/nuscenes/versions/v1.0-mini/scenes/quality
```

The response aggregates readiness buckets, GT coverage, selectable scene counts, and exclusion reasons while also returning paginated per-scene quality rows.

### Dataset quality summary

Dataset quality is a compact aggregate view over scene-level validation/profile/GT quality. It summarizes readiness buckets, GT coverage, selectable scene counts, observed channels, and exclusion reasons. For per-scene details, use `/datasets/{dataset_id}/versions/{version}/scenes/quality`.

```bash
curl http://localhost:8000/api/v1/datasets/nuscenes/versions/v1.0-mini/quality
```

The response includes:
- `readiness` — `ready | warning | blocked | unknown` derived from scene aggregate
- `counts` — scene, sample, frame, annotation, GT scene, and selectable scene counts
- `sceneQuality` — readiness buckets, selectable counts, exclusion reason counts, observed channels
- `groundTruth` — GT scene count, annotated scene count, annotation count, coverage ratio
- `validation` — per-scene readiness bucket counts (not a single run record)
- `profile` — observed channels union across all scene profile runs
- `manifestUri` — dataset manifest artifact URI

---

## Demo: scene-first quality to detection evaluation

SceneOps treats scenes as the primary operational unit. `SceneRecord` rows are the canonical source of scene membership, while `DatasetManifest` is a derived snapshot built from all registered scenes. Validation and profile results are persisted per scene, and dataset quality is computed as a scene aggregate — not as a property of a dataset-level validation run. Detection evaluation then uses the same scene-level view to show which scenes were selected, evaluated, or skipped.

### 1. Ingest and register scenes

```bash
make e2e-dataset-ingestion        # dataset_scene_ingestion pipeline
# optionally also:
make e2e-raw-log-scene-building   # raw_log_scene_building pipeline (non-GT scenes)
```

Runs `ingest_scenes → register_scene → validate_scene → profile_scene → build_scene_index → build_dataset_manifest`. After completion each scene has a `SceneRecord` with GT/count fields and linked `SceneValidationRunRecord` / `SceneProfileRunRecord`. Running both pipelines produces a realistic mixed dataset with GT and non-GT scenes.

### 2. Inspect scene quality

```bash
# Single scene
curl -s http://localhost:8000/api/v1/scenes/<scene_id>/quality | jq

# All scenes for a dataset version (paginated rows + global aggregate)
curl -s http://localhost:8000/api/v1/datasets/nuscenes/versions/v1.0-mini/scenes/quality | jq
```

### 3. Inspect compact dataset quality

```bash
curl -s http://localhost:8000/api/v1/datasets/nuscenes/versions/v1.0-mini/quality | jq
```

Key fields: `readiness`, `counts.sceneCount`, `counts.selectableSceneCount`, `groundTruth.groundTruthCoverageRatio`, `sceneQuality.exclusionReasonCounts`.

### 4. Run detection evaluation

```bash
make e2e-detection-evaluation               # mock backend (no inference server needed)
make e2e-detection-evaluation-real          # real GroundingDINO (requires inference server)
```

### 5. Compare dataset quality and detection run

```bash
make compare-detection PIPELINE_RUN_ID=<pipeline_run_id>
```

Captured output (30-scene dataset: 10 nuScenes GT scenes + 20 raw-log non-GT scenes):

```
=== Dataset Quality ===
  readiness                     : warning
  scene_count                   : 30
  sample_count                  : 808
  annotation_count              : 18538
  ready/warning/blocked/unknown : 30 / 0 / 0 / 0
  selectable_for_detection      : 10
  non_selectable_for_detection  : 20
  ground_truth_scenes           : 10
  gt_coverage_ratio             : 0.3333
  exclusion_reasons             : {"missing_ground_truth":20}

=== Detection Run Comparison ===
  selected_scene_count  : 10
  selected_sample_count : 404
  skipped_scene_count   : 20
  evaluated_scene_count : 10
  ground_truth_count    : 14982
  prediction_count      : 2340
  primary_metric        : precision = 0.318803
```

The compact dataset quality summary predicts that 10 scenes are selectable for detection evaluation and 20 scenes are excluded because they have no GT. The detection comparison confirms the same behavior: 10 scenes are selected/evaluated and 20 scenes are skipped with `scene_has_no_ground_truth`. This connects data quality, scene selection, and evaluation metrics in one operator-facing flow.

> **Note:** `annotation_count` (18538) is the dataset/SceneRecord count across GT scenes, while `ground_truth_count` (14982) is the evaluator-side GT count after evaluation-specific loading and filtering. They can differ.

### 6. Check leaderboard

```bash
curl -s "http://localhost:8000/api/v1/leaderboards/evaluations?dataset_id=nuscenes&dataset_version=v1.0-mini" | jq
```

---

## Key Components

### Pipeline Execution (Worker)

```
PipelineRunner              ← Celery orchestrator (local) / Airflow DAG entry point (future)
└── PipelineTaskRunner      ← single-task use case; also the standalone CLI entry point
    ├── PipelineInputResolver    resolves PipelineTaskInputs from DB records (no in-memory propagation)
    ├── PipelineJobPlanner       builds JobManifest from PipelineTaskInputs
    ├── JobRunner                executes the concrete job by job_id
    ├── PipelineTaskResultBuilder   splits handler output into refs / summary / raw_result
    ├── PipelineTaskResultRecorder  persists normalized PipelineTaskResult to DB
    └── PipelineQualityGate         validates result contract; can block pipeline on failure
```

Standalone task invocation (Airflow-compatible):
```bash
sceneops-worker run-pipeline-task --pipeline-run-id <id> --task-id <task_id>
```

Exit code 0 = success, non-zero = failure. Designed for future DockerOperator / KubernetesPodOperator.

### Scene Manifest

`SceneManifest` is the canonical scene artifact. Key fields:

```
scene_id, dataset_id/version
lineage         ─ raw_log_id, segment_id, source provenance
generation      ─ origin_type (real/synthetic), generation_method
calibrated_sensors  ─ scene-level registry (deduplicated by calibration_id)
ego_poses           ─ scene-level time-varying ego poses
samples         ─ list[SceneSampleManifest]
  └── frames    ─ list[SceneSensorFrameManifest] (references cal + ego_pose by ID)
  └── annotations ─ list[SceneAnnotationManifest] (3D bbox, category, instance_id)
annotation_count, has_ground_truth, ground_truth_source
sample_count, frame_count, channels
```

### Detection Scene Selection

`predict_detection` supports a `scene_selection` param (`DetectionSceneSelectionConfig`) with three modes:

| Mode | Behaviour |
|---|---|
| `all` | All scenes in the dataset manifest |
| `ground_truth_only` | Only scenes where `has_ground_truth=True` and `annotation_count >= min_annotation_count`; optional `ground_truth_sources` filter |
| `explicit_scenes` | Explicit `scene_ids` list |

Common caps: `max_scenes`, `max_samples`, `max_samples_per_scene`.

### Center-Distance Evaluator

`CenterDistanceDetectionEvaluator` (`evaluator_id: "center-distance"`):
- Match threshold: `match_distance_m` (default 2.0 m)
- Skips prediction shards for scenes without GT (`has_ground_truth=False`)
- If the entire dataset has no GT annotations, returns a `skipped` evaluation manifest
- `missing_gt_policy`: `skip` (default) or `fail`

### Async Execution Isolation

Each Celery task spawns a fresh thread with an `AsyncRuntimeRunner` to isolate SQLAlchemy async event loops from Celery's prefork process model. Worker sessions do not auto-commit; job and pipeline state transitions (`RUNNING → SUCCEEDED/FAILED`) are committed explicitly.

---

## Job Registry

### Active (pipeline-wired)

| Job | Pipeline(s) |
|---|---|
| `ingest_scenes` | `dataset_scene_ingestion` |
| `build_scenes` | `raw_log_scene_building` |
| `register_scene` | both scene pipelines |
| `validate_scene` | both scene pipelines |
| `profile_scene` | both scene pipelines |
| `build_scene_index` | both scene pipelines |
| `build_dataset_manifest` | both scene pipelines |
| `predict_detection` | `detection_evaluation` |
| `evaluate_detection` | `detection_evaluation` |

### Planned

```
compare_scenes, mine_scenarios, score_scenario_readiness
auto_label_scene, auto_label_dataset
export_dataset
```

---

## Repository Structure

```
sceneops-platform/
├── apps/
│   ├── api/                        # FastAPI control plane
│   │   └── app/
│   │       ├── platform/           # jobs, pipelines, executions, artifacts
│   │       ├── domains/            # datasets, scenes, models, inference, evaluations, labels
│   │       └── views/              # operations, leaderboards
│   ├── inference-server/           # GroundingDINO server (FastAPI, port 8001; optional)
│   └── worker/
│       └── sceneops_worker/
│           ├── pipelines/          # PipelineRunner, TaskRunner, InputResolver, JobPlanner,
│           │                       #   ResultBuilder, ResultRecorder, QualityGate
│           ├── jobs/               # handlers: dataset/, evaluation/, inference/
│           ├── scenes/             # validator, profiler, raw scene builder, sample grouper,
│           │                       #   selection filter, scene artifacts
│           ├── evaluation/detection/  # CenterDistanceDetectionEvaluator, accumulator,
│           │                          #   loading, artifacts
│           ├── inference/          # mock / ONNX / GroundingDINO + frustum-lift backends
│           ├── core/               # WorkerContext, RunStores, DI
│           ├── execution/          # Celery app factory, job dispatcher
│           ├── tasks/              # Celery task definitions
│           ├── runtime/            # AsyncRuntimeRunner
│           ├── datasets/           # nuScenes ingestion
│           ├── runs/               # run artifact I/O
│           ├── tools/              # adapters and utilities
│           └── tests/              # unit tests (scenes/, evaluation/, inference/, ...)
├── packages/
│   ├── sceneops-core/              # domain schemas, enums, pipeline definitions
│   ├── sceneops-db/                # SQLAlchemy models, async repositories, Alembic
│   └── sceneops-storage/           # LocalArtifactStore, S3ArtifactStore
├── migrations/                     # Alembic versions
├── scripts/
│   ├── e2e/                        # E2E test scripts
│   ├── fixtures/                   # dataset registration
│   ├── checks/                     # env / health checks
│   ├── debug/                      # pipeline/job inspection
│   └── dev/                        # local dev utilities
├── docker-compose.local.yml
├── Makefile
└── pyproject.toml                  # uv workspace (Python 3.11–3.12)
```

---

## Quickstart

**Requirements:** Docker + Docker Compose, [uv](https://github.com/astral-sh/uv), Python 3.11–3.12, nuScenes mini at `./data/raw/nuscenes`.

```bash
cp .env.example .env.local          # configure storage backend, DB, Redis
make setup                          # install deps + pre-commit hooks
make local-up                       # MinIO + Postgres + Redis + API + workers
make register-nuscenes-dataset      # register nuScenes fixture
make e2e                            # run all E2E tests
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

## Common Commands

### Stack

| Command | Description |
|---|---|
| `make local-up` | Start full stack (MinIO + DB + Redis + API + workers) |
| `make local-down` | Stop all services |
| `make local-reset` | Wipe volumes and restart from scratch |
| `make reset-local` | Reset artifacts + DB state without volume wipe |
| `make db-migrate` | Run Alembic upgrade head |
| `make db-revision MSG='...'` | Generate a new migration |

### Development

| Command | Description |
|---|---|
| `make test` | Run worker unit tests |
| `make lint` / `make format` | Ruff check / format |
| `make check` | pre-commit on all files |
| `make worker-imports` | Validate job registry imports |
| `make api-health` | Hit `/health` |

### Worker CLI

| Command | Description |
|---|---|
| `make worker-run-job JOB_ID=...` | Run a job directly |
| `make worker-run-pipeline PIPELINE_RUN_ID=...` | Run a full pipeline |
| `make worker-run-pipeline-task PIPELINE_RUN_ID=... TASK_ID=...` | Run one pipeline task (Airflow entry point) |

### E2E

| Command | Description |
|---|---|
| `make e2e` | All E2E tests (mock backend, no inference server required) |
| `make e2e-api-smoke` | API smoke test |
| `make e2e-dataset-ingestion` | Ingestion pipeline |
| `make e2e-detection-evaluation` | Detection evaluation (mock detector) |
| `make e2e-pipeline-contracts` | PipelineTaskInputs/Result contract validation |
| `make e2e-raw-log-scene-building` | Raw log building (sequence/frame-id/time-bucket mode) |
| `make e2e-detection-evaluation-real` | Detection evaluation with real GroundingDINO inference server |
| `make compare-detection PIPELINE_RUN_ID=<id>` | Print dataset quality + scene comparison for a completed pipeline run |

---

## Limitations

- The default detection E2E (`make e2e-detection-evaluation`) uses a mock detector for deterministic orchestration and evaluation-contract validation.
- A GroundingDINO real-model E2E (`make e2e-detection-evaluation-real`) is available and validates the model-server → prediction artifact → evaluation → leaderboard path end-to-end.
- Current GroundingDINO metrics are integration signals, not production-grade 3D detection benchmark results.
- Scene reconstruction pipeline is defined but not implemented (`supported=False`).
- Auto-labeling is disabled pending a `labeler_id`-based rewrite.
- GroundingDINO inference server is optional (Docker Compose profiles `inference` / `gpu`); not required for mock E2E flows.
- Platform is local-first, intended for architecture validation.

---

## Roadmap

- Airflow DAG integration using `run-pipeline-task` as DockerOperator/KubernetesPodOperator command
- ROS bag / raw-log scene building via `ObservationArtifactStore`
- Dataset sample index artifact
- Scenario mining and readiness scoring
- Auto-labeling with `labeler_id`-based `LabelerRegistry`
- Broaden real detector evaluation scenarios and quality thresholds
- Dataset export (generated/reconstructed scenes)

---

## Tech Stack

| | |
|---|---|
| API | FastAPI, Pydantic v2, Uvicorn |
| Task queue | Celery, Redis 7 |
| Database | PostgreSQL 16, SQLAlchemy 2.0 async, Alembic |
| Artifact storage | MinIO (S3 API), boto3 |
| Inference (optional) | GroundingDINO, HuggingFace Transformers, ONNX Runtime |
| Scene data | nuScenes DevKit |
| Package manager | uv workspace |
| Code quality | Ruff, pre-commit |
| Infra | Docker Compose |
