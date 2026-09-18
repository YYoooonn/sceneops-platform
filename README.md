# SceneOps Platform

SceneOps Platform is a local-first robotics data and MLOps platform for scene-centric dataset management, dataset quality evaluation, model inference/evaluation, and scenario curation.

It uses nuScenes mini as a realistic autonomous-driving dataset and implements production-like components such as FastAPI control plane, PostgreSQL metadata store, object-storage-style artifact management, Celery-based async execution, and pipeline/job orchestration.

> Modern robotics AI systems require more than model inference. They need reliable sensor data ingestion, scene-level quality control, reproducible evaluation, artifact lineage, and data selection workflows for model improvement.
>
> SceneOps explores this problem as a small but production-shaped platform.

**v2** extends the platform toward a general robotics data source: a real ROS2 (Jazzy) sandbox replays nuScenes CAN bus data as ROS2 topics, records it with `rosbag2`/MCAP, and an adapter decodes the resulting bag (real CDR encoding, not a mock) into `RobotState`/`Mission` rows and, through a dedicated pipeline, task-oriented `Episode` records — with a REST API, a Parquet analytics export, and DuckDB query support on top. See [Demo 4](#demo-4-robot-data-ingestion-ros2--can-replay--rosbag2mcap) and [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md).

For the full documentation set (architecture, data model, Scene/Episode domain flow, jobs/pipelines, storage, quality, reserved architecture), start at [`docs/architecture/overview.md`](docs/architecture/overview.md).

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
| Scene registry              | ✅              | Canonical `SceneRecord` catalog                |
| Dataset manifest            | ✅              | DB-backed derived manifest                     |
| Scene validation/profile    | ✅              | Per-scene quality runs                         |
| Dataset quality             | ✅              | Scene-quality aggregate                        |
| Scene quality APIs          | ✅              | Scene and dataset-version quality views        |
| **Episode domain (v2)**    | ✅              | Robot rosbag/MCAP → task-oriented `EpisodeRecord`, segmented by mission boundaries |
| **Episode validation/profile (v2)** | ✅     | Per-episode quality runs, same pattern as Scene |
| **Episode quality API (v2)** | ✅            | `GET /episodes/{id}/quality`, derived from run records, not status |
| Mock detection             | ✅              | Fast contract-test backend                     |
| Real detection             | ✅              | GroundingDINO inference backend                |
| Detection evaluation       | ✅              | Metrics, artifacts, leaderboard                |
| Detection comparison       | ✅              | Quality → selection → evaluation debug view    |
| Scenario curation          | ✅ Experimental | Scene mining + readiness scoring               |
| Scenario records/artifacts | ✅              | ScenarioSet + scenario run records             |
| Reliable batch execution   | ✅              | `execution_key` idempotency, partial retry, per-task quality gate |
| Airflow pipeline backend   | ✅ PoC          | Per-task DAG execution (`dataset_scene_ingestion` only) as an alternate dispatch backend to Celery |
| Analytics export (Parquet) | ✅              | Dataset + robot tables via Polars/PyArrow; DuckDB SQL query support |
| E2E scripts                | ✅              | Dataset, detection, comparison, curation, robot CAN-replay flows |
| Operations views           | ✅              | Summary, timeline, failures                    |
| Leaderboards               | ✅              | Evaluation/model/dataset rankings              |
| **ROS2 robot data ingestion (v2)** | ✅      | CAN replay → real ROS2 topics → rosbag2/MCAP → `RobotState`/`Mission` |
| **Robot domain API (v2)**  | ✅              | `Robot`/`RobotRun` REST registration, `Mission`/`RobotState` query    |

The current platform demonstrates five end-to-end workflows:

1. **Scene-first dataset quality → scenario curation → detection evaluation**
  Scene-level quality signals drive scenario curation, which constrains detection evaluation to a curated ScenarioSet.
2. **Real detection evaluation**
  GroundingDINO evaluates only scenes selected by the ScenarioSet, with full lineage recorded in inference and evaluation run metadata.
3. **Scenario curation**
  Scene quality signals are converted into scenario candidates and readiness scores, producing a ScenarioSet artifact.
4. **Robot data ingestion (v2)**
  A real ROS2 node replays nuScenes CAN bus data as ROS2 topics, records it as a rosbag2/MCAP file, and an adapter decodes it (real CDR messages, not a synthetic fixture) into `RobotState`/`Mission` rows queryable through a REST API and a Parquet+DuckDB analytics export.
5. **Episode building (v2)**
  The same decoded rosbag/MCAP recording is independently segmented into task-oriented `Episode` records (observation+action windows, split at mission boundaries by default), registered, validated, and profiled — a separate pipeline and record type from both Scene and RobotState. See [Demo 5](#demo-5-episode-building-from-a-robot-recording).

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

Detection evaluation runs model predictions against a scene selection.

When a `ScenarioSet` is provided, detection evaluates only scenes selected by that ScenarioSet.
Within the selected scenes, existing scene-quality filters (validation status, GT availability, annotation count) still apply.
Scenes outside the ScenarioSet are skipped with reason `not_in_scenario_set`.
ScenarioSet lineage is recorded in both inference and evaluation run metadata, making evaluation results explainable.

### Scenario

A `Scenario` is a purpose-specific, derived view of a scene or a group of scenes.

It is not a new raw data unit. It represents a curated candidate for downstream use cases such as evaluation, reconstruction, pseudo-labeling, or model debugging.

### ScenarioSet

A `ScenarioSet` is an artifact-backed curation result.

It groups mined scenario candidates and stores readiness scores, selection reasons, and report artifacts generated by the scenario curation pipeline.

### Robot / RobotRun / Mission / RobotState (v2)

A separate domain from Dataset/Scene, for robot runtime data rather than pre-recorded sensor datasets.

```text
Robot        static registry entry (robot_id, platform)
RobotRun     one physical recording session (maps 1:1 to a rosbag2/MCAP file)
Mission      a run's lifecycle status (pending/running/completed/...), extracted from the bag
RobotState   a robot-runtime-state time series (position, orientation, velocity, battery, ...)
```

`Robot`/`RobotRun` are registered directly (POST, no Job involved — same tier as Dataset registration). `Mission`/`RobotState` are populated by the `ingest_robot_states` Job, which reads a rosbag2/MCAP file through `RosbagAdapter`.

### Episode (v2)

An `Episode` is a task-oriented observation+action window segmented out of a robot recording — a separate domain from Scene (snapshot-style sensor observation) and from RobotState (raw runtime time series). `EpisodeRecord` is the canonical unit, built by the `raw_log_episode_building` pipeline (`build_episodes → register_episode → validate_episode → profile_episode`) from the same rosbag/MCAP recording `RosbagAdapter` decodes for robot ingestion.

`EpisodeStatus` deliberately stays a registration-only lifecycle (`created`/`registered`) — quality/readiness is always derived live from the latest validation/profile run record, never cached on the record's own status. See [`docs/architecture/episode-domain.md`](docs/architecture/episode-domain.md).

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
| Analytics export | `packages/sceneops-analytics` | Polars/PyArrow table builders, Parquet writer, DuckDB query helper                                       |
| Inference server | `apps/inference-server`     | Optional GroundingDINO inference server                                                                   |
| Robot sandbox (v2) | `ros2/`                   | ROS2 Jazzy Docker environment + `CanReplayNode` (not an `apps/` service — a dev-time data source)          |


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

`raw_log_episode_building` (v2)
  build_episodes → register_episode → validate_episode → profile_episode

`scene_registration`
  register_scene → validate_scene → profile_scene
  (same tail as the two pipelines above, without a build/ingest head — for scenes a generated/reconstructed/simulated process already produced a manifest for)

#### Standalone robot jobs (v2)

Robot/RobotRun is a separate domain from Dataset/DatasetVersion (see [Core concepts](#robot--robotrun--mission--robotstate-v2)), so these dispatch as plain Jobs, not as part of a named pipeline:

`ingest_robot_states`
  reads a rosbag2/MCAP file via `RosbagAdapter` → `RobotState` + `Mission` rows

`export_robot_analytics_snapshot`
  exports one RobotRun's `RobotState`/`Mission` rows to `robot_telemetry.parquet` / `missions.parquet`

### Robot data ingestion path (v2)

```text
nuScenes CAN bus data
  └─ CanReplayNode (real rclpy, ros2/ Docker sandbox)
       └─ ROS2 topics ── /vehicle/odom, /vehicle/imu, /vehicle/status, /vehicle/control, /mission/status
            └─ ros2 bag record --storage mcap
                 └─ rosbag2/MCAP file
                      └─ RosbagAdapter (apps/worker) ── decodes real CDR messages, no rclpy needed to read
                           └─ ingest_robot_states Job ─► Postgres (RobotState, Mission)
                                └─ export_robot_analytics_snapshot Job ─► Parquet (Artifact Store)
                                     └─ DuckDB query (sceneops_analytics.query_parquet)
```

Standard ROS2 messages (`nav_msgs/Odometry`, `sensor_msgs/Imu`, `sensor_msgs/BatteryState`) are used where they fit; `/vehicle/control` and `/mission/status` have no matching standard message, so `CanReplayNode` publishes them as `std_msgs/String` carrying flat JSON — `RosbagAdapter` recognizes the schema and unwraps it, rather than requiring a custom `.msg` colcon package.

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

## Demo 1: scene-first quality → scenario curation → detection evaluation

SceneOps treats `SceneRecord` as the canonical source.
Dataset quality aggregates scene-level validation, profile, and GT signals into a readiness view.
Scenario curation converts those signals into a `ScenarioSet` — a curated selection artifact over registered `SceneRecord`s.
Detection evaluation then runs only on scenes selected by the ScenarioSet, while existing scene-quality filters (GT availability, annotation count) still apply within that set.
ScenarioSet lineage is recorded in both inference and evaluation run metadata.

> A ScenarioSet is not a new raw data unit. It is a curated selection artifact over existing SceneRecords.

### Quickstart

```bash
make e2e-dataset-ingestion        # dataset_scene_ingestion pipeline (10 nuScenes GT scenes)
make e2e-raw-log-scene-building   # raw_log_scene_building pipeline (20 non-GT scenes)
make inference-local-up
make e2e-scenario-curation        # prints scenario_set_id and scenario curation pipeline_run_id
make e2e-detection-evaluation-real SCENARIO_CURATION_PIPELINE_RUN_ID=pipe-...
make compare-detection PIPELINE_RUN_ID=<detection_pipeline_run_id>
```

Or pass the ScenarioSet ID directly:

```bash
make e2e-detection-evaluation-real SCENARIO_SET_ID=scset-...
```

> `SCENARIO_CURATION_PIPELINE_RUN_ID` is the pipeline run ID printed by `e2e-scenario-curation`.
> `PIPELINE_RUN_ID` in `compare-detection` is the detection-evaluation pipeline run ID — a different run.

### Example output — 30-scene dataset: 10 GT (nuScenes) + 20 non-GT (raw-log-style)

Scenario curation mines the 10 GT scenes into a ScenarioSet (candidate_count=10, rejected_count=20).
Detection evaluation uses that ScenarioSet to constrain scene selection.

```
=== Scenario Curation Result ===
  pipeline_run_id : pipe-...
  scenario_set_id : scset-...
  candidate_count=10  ready=8  warning=2  blocked=0

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

=== ScenarioSet Lineage ===
  scenario_set_id          : scset-...
  scenario_candidate_count : 10
  scenario_selected_count  : 10
  scenario_rejected_count  : 20
  not_in_scenario_set      : 20
  lineage_consistency      : ok
  flow                     : 10 scenario candidates → 10 selected scenes → 10 ev

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
ScenarioSet candidates constrain prediction scene selection.
Scenes outside the ScenarioSet are skipped with not_in_scenario_set.
Scenes inside the ScenarioSet pass through existing GT/quality filters.
scenario_set_id is recorded in both inference and evaluation run metadata.

ScenarioSet candidates → selected scenes → evaluated scenes
10 scenario candidates → 10 selected scenes → 10 evaluated scenes
```

`readiness=warning` is expected when only 10/30 scenes are selectable for detection.

`annotation_count` (18538) is the dataset-level count from `SceneRecord`;
`ground_truth_count` (14982) is the evaluator-side count after evaluation-specific loading and filtering
— these values can differ.

The precision value (0.318803) reflects real GroundingDINO detections on this limited nuScenes mini sample, not a production benchmark.

---

## Demo 2: real GroundingDINO detection evaluation

SceneOps supports both a fast mock backend and a real GroundingDINO backend.
The real E2E target requires a ScenarioSet — either a direct ID or a scenario curation pipeline run ID.

```bash
make local-up
make inference-local-up   # or make inference-gpu-up for GPU
make e2e-dataset-ingestion
make e2e-scenario-curation
make e2e-detection-evaluation-real SCENARIO_CURATION_PIPELINE_RUN_ID=pipe-...
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

The script prints both `pipeline_run_id` and `scenario_set_id` on completion.
Either can be passed directly to real detection evaluation:

```bash
# Use the printed pipeline_run_id:
make e2e-detection-evaluation-real SCENARIO_CURATION_PIPELINE_RUN_ID=pipe-...

# Or use the printed scenario_set_id directly:
make e2e-detection-evaluation-real SCENARIO_SET_ID=scset-...
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

## Demo 4: robot data ingestion (ROS2 → CAN replay → rosbag2/MCAP)

Unlike Demos 1–3, this path doesn't start from a pre-recorded dataset. A real ROS2 node replays nuScenes CAN bus messages (`pose`, `ms_imu`, `vehicle_monitor`) as ROS2 topics in real time, `ros2 bag record` captures them into a genuine MCAP file, and `RosbagAdapter` decodes that file — real CDR-encoded ROS2 messages, using the schema embedded in the MCAP file itself, no `rclpy` required to read it back.

> Requires the nuScenes CAN bus expansion at `data/raw/nuscenes/can_bus/` (separate download from nuScenes mini itself — see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md)).

### Quickstart

```bash
make local-up
make ros2-up                        # ROS2 Jazzy sandbox (rclpy, rosbag2, MCAP storage plugin)
make e2e-robot-can-replay           # CAN replay → record → register → ingest, verified via API
```

Or step by step:

```bash
make ros2-can-replay-record SCENE=scene-0061 RATE=5.0
make worker-register-robot-run ROBOT_ID=robot-1 RUN_ID=run-1 MCAP_URI=/data/raw/rosbag/scene-0061/scene-0061_0.mcap
# then dispatch `ingest_robot_states` via POST /api/v1/jobs, same as any other job
```

### Example output — scene-0061, 2875 CAN messages replayed at 10x

```
=== ingest_robot_states job result ===
robot_id       : robot-nuscenes-01
robot_run_id   : run-scene-0061
state_count    : 2913
mission_count  : 1

=== GET /missions?robot_run_id=run-scene-0061 ===
mission_id : mission-scene-0061
status     : completed
started_at : 2026-09-03T06:27:36Z
ended_at   : 2026-09-03T06:27:42Z

=== export_robot_analytics_snapshot job result ===
table_uris.robot_telemetry : s3://sceneops/artifacts/analytical/robot_runs/run-scene-0061/robot_telemetry.parquet
table_uris.missions         : s3://sceneops/artifacts/analytical/robot_runs/run-scene-0061/missions.parquet
row_counts                  : {robot_telemetry: 2913, missions: 1}

=== DuckDB query over the exported Parquet (robot_telemetry JOIN missions) ===
mission_id            status      telemetry_row_count  avg_battery
mission-scene-0061    completed   2913                 0.91
```

`state_count` (2913) is one row per CAN message (`pose`→odom, `ms_imu`→imu, `vehicle_monitor`→status+control), not one row per mission — a single `RobotRun` accumulates many `RobotState` rows. The DuckDB query reads real Parquet files downloaded from MinIO, not an in-memory fixture.

**Current limitations:**

- Binary sensor payloads (`sensor_msgs/Image`, `PointCloud2`) decode via CDR but aren't written to files yet — no real camera/LiDAR-publishing node exists to test against
- `/vehicle/control` and `/mission/status` use a `std_msgs/String` + JSON bridge instead of a proper custom `.msg` package (would need a `colcon` build step)
- No live robot control — this is batch ingestion of a recording, not real-time command/control (see `docs/adr/005-ros2-vs-kafka-boundary.md`)

See [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md) for the full ingestion flow and current limitations.

---

## Demo 5: episode building from a robot recording

The same decoded rosbag/MCAP recording that feeds `ingest_robot_states` also feeds a separate pipeline, `raw_log_episode_building`, which segments it into task-oriented `Episode` records instead of a raw telemetry time series. Segmentation defaults to one Episode per dated Mission (`EpisodeSegmentationStrategy.MISSION_BOUNDARY`); `WHOLE_RUN` and `FIXED_WINDOW` are also supported.

```bash
make local-up
make ros2-up
make e2e-episode-building   # reuses the committed MCAP fixture from e2e-robot-can-replay — no live ROS2 sandbox required
```

**Pipeline:** `build_episodes → register_episode → validate_episode → profile_episode`

**Example output:**

```
=== build_episodes job result ===
episode_count : 1

=== register_episode job result ===
registered_episode_count : 1

=== GET /episodes/{episode_id}/quality ===
status     : registered
readiness  : ready
frame_count: 2913
observation_channels : [position, orientation, velocity, battery]
action_channels       : [steering, throttle, brake]
```

`status=registered` never encodes quality — `readiness` is always derived live from the latest validation run, not cached on the Episode record itself. See [`docs/architecture/episode-domain.md`](docs/architecture/episode-domain.md).

---

## API overview

All API routes are under `/api/v1`. Health is exposed at the root.

SceneOps exposes APIs across five main surfaces:

| Surface          | Areas                                       | Purpose                                                      |
| ---------------- | ------------------------------------------- | ------------------------------------------------------------ |
| Data catalog     | Datasets, Scenes, Episodes, Scenarios, Models, Labels | Register and inspect data/model resources           |
| Robots (v2)      | Robots, RobotRuns, Missions, RobotStates    | Register robots/runs; query ingested telemetry and mission history |
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
GET  /api/v1/episodes                                  # filter by dataset_id/robot_run_id/mission_id
GET  /api/v1/episodes/{episode_id}/quality
GET  /api/v1/scenarios/{scenario_set_id}/artifacts
GET  /api/v1/models/{model_id}/versions/{v}

# Robots (v2)
POST /api/v1/robots
POST /api/v1/robot-runs
GET  /api/v1/missions?robot_run_id={id}
GET  /api/v1/robot-states?robot_run_id={id}

# Execution
POST /api/v1/pipelines/runs
POST /api/v1/pipelines/runs/{id}/execute
GET  /api/v1/jobs/{job_id}/events
GET  /api/v1/executions/{execution_id}

# Runs and artifacts
GET  /api/v1/inference/runs/{id}/artifacts
GET  /api/v1/evaluations/runs/{id}/metrics
GET  /api/v1/artifacts/{artifact_id}
GET  /api/v1/artifacts?owner_type=robot_run&owner_id={id}

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
make local-up                       # idempotent: Postgres + Redis + MinIO + migrate + API + workers
make register-nuscenes-dataset      # register nuScenes fixture
make test                           # infrastructure-independent unit tests
make test-integration               # real Postgres + MinIO tests
make e2e                            # full default-stack E2E suite (mock backend) -- see [docs/development/local-development.md](docs/development/local-development.md)
```

**Robot data ingestion (v2, optional):** requires the nuScenes CAN bus expansion unzipped at `data/raw/nuscenes/can_bus/` (a separate download from nuScenes mini — see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md)). Everything else is self-contained in the `ros2/` Docker image.

```bash
make ros2-up                        # ROS2 Jazzy sandbox
make e2e-robot-can-replay           # CAN replay → rosbag2/MCAP → ingest, verified via API
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

See [`docs/development/local-development.md`](docs/development/local-development.md) for the full bootstrap/reset/env-file model.

| Command             | Description                                           |
| ------------------ | ----------------------------------------------------- |
| `make local-up`    | Idempotent bootstrap: infra → health → MinIO buckets → migrate → API + workers |
| `make local-down`  | Stop services, **preserve** Postgres/Redis/MinIO data  |
| `make local-reset` | **Destructive** — wipe all local data, rebuild clean   |
| `make status` / `make logs` | Service status / follow logs                 |
| `make db-migrate`  | Run Alembic upgrade head (also run by `local-up`)      |


### Development


| Command                     | Description                   |
| --------------------------- | ----------------------------- |
| `make test`                 | worker + api + sceneops-core + sceneops-analytics + inference-server unit tests — no infra needed |
| `make test-integration`     | sceneops-db (real Postgres) + sceneops-storage (real MinIO) — requires `make local-up` |
| `make lint` / `make format` | Ruff check / format           |
| `make worker-imports`       | Validate job registry imports |


### E2E

`make e2e` runs the full default-stack suite (see [docs/development/local-development.md](docs/development/local-development.md)) — every E2E whose services are covered by `make local-up` alone. Airflow/ROS2/real-inference E2Es need extra setup and stay outside it.

|  Command | Description |
| --- | --- |
| `make e2e` | Full default-stack suite: api-smoke + pipeline-contracts + dataset-ingestion + raw-log-scene-building + episode-building + scenario-curation + detection-evaluation (mock) + analytics-export + reliability |
| `make e2e-api-smoke` | API smoke |
| `make e2e-dataset-ingestion` | Ingestion pipeline |
| `make e2e-raw-log-scene-building` | Raw log scene building |
| `make e2e-detection-evaluation` | Detection evaluation (mock) |
| `make e2e-scenario-curation` | Scenario curation pipeline; prints `scenario_set_id` and `pipeline_run_id` |
| `make e2e-detection-evaluation-real SCENARIO_SET_ID=scset-...` | *(optional environment)* Detection evaluation with real GroundingDINO; requires `make inference-local-up`/`inference-gpu-up` and `SCENARIO_SET_ID` or `SCENARIO_CURATION_PIPELINE_RUN_ID` |
| `make e2e-detection-evaluation-real SCENARIO_CURATION_PIPELINE_RUN_ID=pipe-...` | Same as above; resolves ScenarioSet from the curation pipeline run |
| `make e2e-pipeline-contracts` | Pipeline contract validation  |
| `make e2e-episode-building` | Episode domain build → register → validate → profile pipeline |
| `make e2e-analytics-export` | Analytics snapshot export |
| `make e2e-reliability` | Asserts pipeline execution-key dedup/force semantics |
| `make e2e-airflow-pipeline` | *(optional environment)* Same pipeline, dispatched via Airflow — requires `make airflow-up` |
| `make compare-detection PIPELINE_RUN_ID=<detection_pipeline_run_id>` | Dataset quality + detection run comparison; includes ScenarioSet lineage when available |
| `make e2e-robot-can-replay SCENE=scene-0061 RATE=10.0` | *(optional environment, ROS2)* Robot data ingestion (v2): CAN replay → record → register → ingest → verify via API |


### ROS2 / Robot (v2)


| Command | Description |
| --- | --- |
| `make ros2-up` / `ros2-down` | Start/stop the ROS2 Jazzy sandbox container |
| `make ros2-shell` | Interactive shell in the sandbox (`ros2` CLI, `rclpy` on PATH) |
| `make ros2-check` | Smoke-test `rclpy` import + MCAP storage plugin |
| `make ros2-can-replay SCENE=scene-0061 RATE=10.0` | Replay nuScenes CAN data as ROS2 topics (no recording) |
| `make ros2-can-replay-record SCENE=scene-0061 RATE=5.0 DURATION=30` | Same, recorded to `data/raw/rosbag/<scene>/` as MCAP |
| `make worker-register-robot-run ROBOT_ID=.. RUN_ID=.. MCAP_URI=..` | Register a Robot + RobotRun via CLI (alternative to `POST /robots`) |


---

## Repository structure

```
sceneops-platform/
├── apps/
│   ├── api/                        # FastAPI control plane
│   │   └── app/
│   │       ├── platform/           # jobs, pipelines, executions, artifacts
│   │       ├── domains/            # datasets, scenes, episodes (v2), models, scenarios, inference, evaluations, robots (v2)
│   │       └── views/              # operations, leaderboards
│   ├── inference-server/           # GroundingDINO server (FastAPI, port 8001; optional)
│   └── worker/
│       └── sceneops_worker/
│           ├── pipelines/          # PipelineRunner, TaskRunner, InputResolver, Planner,
│           │                       #   ResultBuilder, ResultRecorder, QualityGate
│           ├── jobs/dataset/       # handlers: scene + episode (v2) build/register/validate/profile, dataset aggregation
│           ├── jobs/               # evaluation/, inference/, scenarios/, robots/ (v2)
│           ├── scenes/             # validator, profiler, raw scene builder, selection filter
│           ├── datasets/ingestion/ # NuScenesRawLogMocker, RosbagAdapter (v2, real CDR decoding)
│           ├── evaluation/detection/  # CenterDistanceDetectionEvaluator, accumulator
│           ├── inference/          # mock / ONNX / GroundingDINO + frustum-lift backends
│           ├── stores/robots.py    # RobotStore (v2)
│           ├── cli/robots.py       # `sceneops-worker robots register-run` (v2)
│           ├── core/               # WorkerContext, stores, DI
│           ├── execution/          # Celery app factory, job dispatcher
│           └── tests/              # unit tests
├── packages/
│   ├── sceneops-core/              # domain schemas, enums, pipeline definitions (incl. episodes/, robots/ — v2)
│   ├── sceneops-db/                # SQLAlchemy models, async repositories, Alembic
│   ├── sceneops-storage/           # LocalArtifactStore, S3ArtifactStore
│   └── sceneops-analytics/         # Parquet table builders, DuckDB query helper (v2 adds robot tables)
├── ros2/                           # (v2) ROS2 Jazzy Docker sandbox
│   ├── Dockerfile                  #   rclpy, rosbag2, MCAP storage plugin, nuscenes-devkit
│   └── nodes/can_replay_node.py    #   CanReplayNode — nuScenes CAN → real ROS2 topics
├── migrations/                     # Alembic versions
├── scripts/
│   ├── e2e/                        # E2E scripts (incl. e2e_robot_can_replay.sh, e2e_episode_building.sh — v2)
│   ├── fixtures/                   # dataset registration
│   └── debug/                      # pipeline/job inspection
├── docs/
│   ├── architecture/               # overview, data-model, scene-domain, episode-domain (v2),
│   │                                #   jobs-and-pipelines, storage-layout, quality-and-runs, reserved-and-limitations
│   ├── workflows/                  # robot-run-and-mcap.md (v2)
│   ├── development/                # local-development.md
│   └── adr/                        # architecture decision records
├── docker-compose.local.yml
├── Makefile
└── pyproject.toml                  # uv workspace (Python 3.11–3.12)
```

---

## Limitations and roadmap

See [`docs/architecture/reserved-and-limitations.md`](docs/architecture/reserved-and-limitations.md) for the full, code-verified list, including intentionally reserved architecture (unimplemented-but-retained JobTypes, `WORLD_STATE`, `JOB_STEP_DEFINITIONS_BY_TYPE`) that looks unwired but isn't dead code.

### Current limitations

* The default local dataset is nuScenes mini.
* **(v2)** Episode has no `DatasetManifest`-equivalent index and no Parquet analytics table, and no `selectable_for_*` concept the way Scene has for detection evaluation.
* The platform is local-first and optimized for architecture validation, not large-scale production throughput.
* GroundingDINO evaluation results are integration signals, not production model benchmarks.
* Scenario curation is implemented but still marked `experimental=True`.
* Scenario candidates are artifact-backed; there is no per-scenario item table yet.
* Scenario readiness scoring currently uses metadata and scene-quality signals, not image/LiDAR content.
* Scene reconstruction, auto-labeling, and generated dataset preparation pipelines are defined but not implemented.
* Operations and leaderboard APIs exist, but there is no dedicated web UI yet.
* The Airflow pipeline backend is a per-task DAG PoC hardcoded to `dataset_scene_ingestion`; other pipeline types still only run through Celery.
* **(v2)** Binary sensor payloads (`sensor_msgs/Image`, `PointCloud2`) decode via CDR but aren't written to files yet — no real camera/LiDAR-publishing ROS2 node exists to test against.
* **(v2)** `/vehicle/control` and `/mission/status` use a `std_msgs/String` + JSON bridge, not a proper custom `.msg` package (would need a `colcon` build step).
* **(v2)** Robot data ingestion is batch/post-hoc (replay → record → decode → ingest) — there is no live robot control or real-time telemetry streaming; see `docs/adr/005-ros2-vs-kafka-boundary.md` for the intended boundary once that's built.
* **(v2)** DuckDB queries only work against locally-downloaded Parquet files; querying S3/MinIO-backed artifacts directly would need DuckDB's httpfs/S3 extension, which isn't wired up.

### Roadmap

* Generalize the Airflow per-task DAG PoC beyond `dataset_scene_ingestion` to other pipeline types.
* Evaluation-aware scenario mining using FP/FN and per-scene metric signals.
* Pseudo-label candidate workflow for no-GT or weakly labeled scenes.
* VLM-based semantic scene tagging.
* Scene reconstruction package export.
* Auto-labeling pipeline with a labeler registry.
* Per-scenario item table for queryable scenario candidates and review status.
* Web UI on top of existing operations and leaderboard APIs.
* Cloud object storage hardening, including stronger artifact lifecycle and integrity checks.
* **(v2)** Write decoded camera/LiDAR payloads to the Artifact Store and wire `RosbagAdapter` into `build_scenes` for full `SceneRecord` registration from robot data, not just `RobotState`/`Mission`.
* **(v2)** A real custom ROS2 `.msg` package for `/vehicle/control` and `/mission/status`, replacing the JSON-over-`std_msgs/String` bridge.
* **(v2)** A ROS2 Data Gateway bridging live ROS2 topics to Kafka for real-time telemetry (roadmap Phase 7 / `ADR-005`), and eventual live robot control as its own service — not `apps/worker`.
* **(v2)** Scale-testing with synthetic multi-robot telemetry (roadmap Phase 5: N virtual robots) to compare local (Polars/DuckDB) vs. distributed (Spark) processing.

---

## Tech stack


|                      |                                                       |
| -------------------- | ----------------------------------------------------- |
| API                  | FastAPI, Pydantic v2, Uvicorn                         |
| Task queue           | Celery, Redis 7                                       |
| Database             | PostgreSQL 16, SQLAlchemy 2.0 async, Alembic          |
| Artifact storage     | MinIO (S3 API), boto3                                 |
| Batch orchestration  | Airflow 2.10 (PoC pipeline backend, `DockerOperator`) |
| Analytics            | Polars, PyArrow, DuckDB                               |
| Inference (optional) | GroundingDINO, HuggingFace Transformers, ONNX Runtime |
| Scene data           | nuScenes DevKit                                       |
| Robot data (v2)      | ROS2 Jazzy, rclpy, rosbag2, MCAP (`mcap`, `mcap-ros2-support`), nuScenes CAN bus |
| Package manager      | uv workspace                                          |
| Code quality         | Ruff, pre-commit                                      |
| Infra                | Docker Compose                                        |
