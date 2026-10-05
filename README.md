# SceneOps Platform

SceneOps Platform is a local-first robotics data and MLOps platform for scene-centric dataset management, dataset quality evaluation, model inference/evaluation, and scenario curation.

It uses nuScenes mini as a realistic autonomous-driving dataset and implements production-like components such as FastAPI control plane, PostgreSQL metadata store, object-storage-style artifact management, Celery-based async execution, and pipeline/job orchestration.

> Modern robotics AI systems require more than model inference. They need reliable sensor data ingestion, scene-level quality control, reproducible evaluation, artifact lineage, and data selection workflows for model improvement.
>
> SceneOps explores this problem as a small but production-shaped platform.

**v2** extends the platform toward a general robotics data source: a robot's data enters as an L1 raw recording (an MCAP) acquired either in batch (an external dataset converted by `tools/dataset-acquisition`) or by streaming (ROS2 topics → bridge → Kafka → capture), is registered as a `RobotRun`, and is canonicalized into Scenes and Episodes — with a REST API, a Parquet analytics export, and DuckDB query support on top. See [Demo 4](#demo-4-robot-data-acquisition-batch-and-streaming) and [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md).

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
| Derived perception layer    | ✅              | Label sets, sample views, ScenarioSets, pinned detection / evaluation revisions |
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
| Airflow pipeline backend   | ✅ PoC          | Per-task DAG execution (`recording_scene_building` only) as an alternate dispatch backend to Celery |
| Analytics export (Parquet) | ✅              | Dataset + robot tables via Polars/PyArrow; DuckDB SQL query support |
| E2E scripts                | ✅              | Dataset, detection, comparison, curation, batch and streaming acquisition flows |
| Operations views           | ✅              | Summary, timeline, failures                    |
| Leaderboards               | ✅              | Evaluation/model/dataset rankings              |
| **Robot data acquisition (v2)** | ✅      | batch or streaming (ROS2 → Kafka → capture) → L1 MCAP → `RobotRun` → Scenes / Episodes |
| **Robot domain API (v2)**  | ✅              | `Robot`/`RobotRun` REST registration, `Mission`/`RobotState` query    |

The current platform demonstrates five end-to-end workflows:

1. **Scene-first dataset quality → scenario curation → detection evaluation**
  Scene-level quality signals drive scenario curation, which constrains detection evaluation to a curated ScenarioSet.
2. **Real detection evaluation**
  GroundingDINO evaluates only scenes selected by the ScenarioSet, with full lineage recorded in inference and evaluation run metadata.
3. **Scenario curation**
  Scene quality signals are converted into scenario candidates and readiness scores, producing a ScenarioSet artifact.
4. **Robot data ingestion (v2)**
  nuScenes is acquired in batch (MCAP) or by paced ROS2 replay through the bridge, Kafka and capture; both yield equivalent canonical Scenes and Episodes, and robot telemetry is queryable through a REST API and a Parquet+DuckDB analytics export.
5. **Episode building (v2)**
  The same decoded rosbag/MCAP recording is independently segmented into task-oriented `Episode` records (observation+action windows, split at mission boundaries by default), registered, validated, and profiled — a separate pipeline and record type from both Scene and RobotState. See [Demo 5](#demo-5-episode-building-from-a-robot-recording).

---

## Core concepts

SceneOps is designed around a scene-first data model.

```text
SceneRecord / EpisodeRecord = canonical units (source-faithful, asynchronous)
LabelSet                    = post-acquisition labels, independent lineage
SceneSampleView             = derived, policy-driven synchronization of one Scene
ScenarioSet                 = curated selection over sample views
prediction / evaluation     = derived results that pin the revisions they consumed
```

Every derived object is an immutable, checksum-pinned revision (ADR-007 §33): it records the exact revisions it was built from, and a consumer resolves a pin, never "the latest".

### Scene

A `Scene` is the canonical unit of registered sensor data: every observation of its channels, each on its own clock, plus calibration and poses. It carries no ground truth and no synchronization. `SceneRecord` is its catalog row and pins one manifest revision.

### Label set

A `LabelSet` holds post-acquisition labels (human, external or model-generated) anchored on canonical observations by `(robot_run_id, channel, source_clock, timestamp_ns)`, plus the coverage it annotated. It is imported with `IMPORT_LABELS`, never written into a Scene, and every import is a new immutable revision. Format adapters live outside the platform (`dataset-acquisition nuscenes-labels`).

### Scene sample view

A `SceneSampleView` derives, from one Scene revision, an explicit policy for sampling and association: which channel defines the sample instants, which other channels and which ego pose are associated (nearest / previous, with a tolerance, never across clocks), and which label revisions attach. Built by `BUILD_SCENE_SAMPLE_VIEWS`.

### Scene and dataset quality

Quality is canonical Scene quality only: validation readiness, observed channels and counts. Labels and detection selectability are derived concerns of label sets and sample views.

### ScenarioSet

A `ScenarioSet` is a mined, ordered selection over pinned sample views with explicit curation criteria (label counts, required channels, Scene readiness). It is an immutable revision; `scenario_curation` mines and scores it.

### Detection and evaluation

`predict_detection` runs on a ScenarioSet or explicit sample views and publishes a prediction revision that pins its inputs and configuration. `evaluate_detection` scores one prediction revision against one pinned label revision, only on samples the label set covers; a prediction and a label in different frames fail instead of being compared.

### Robot / RobotRun / Mission / RobotState (v2)

A separate domain from Dataset/Scene, for robot runtime data rather than pre-recorded sensor datasets.

```text
Robot        static registry entry (robot_id, platform)
RobotRun     immutable provenance of one finalized, published and verified MCAP recording
Mission      a run's lifecycle status (pending/running/completed/...), extracted from the bag
RobotState   a robot-runtime-state time series (position, orientation, velocity, battery, ...)
```

`Robot` is registered directly (`POST /robots`). A `RobotRun` exists only once the `register_robot_run` Job has verified a RobotRunManifest written by the database-free Recording Publisher. It has no status and is never updated. `Mission`/`RobotState` are populated by the `ingest_robot_states` Job, which reads the RobotRun's registered recording through the verified recording resolver. See [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md).

### Episode (v2)

An `Episode` is a canonical, task/behavior-oriented projection of one registered RobotRun recording: its observation, state, action and task/event streams, kept asynchronous, each on its own declared clock. It is a sibling of Scene over the same RobotRun, built by the `recording_episode_building` pipeline (`build_recording_episodes → register_episodes → validate_episode → profile_episode`) from a build configuration that names topics, fields, clocks and segmentation. Temporal alignment is a derived step (`align_episode` → `AlignedEpisode`). `EpisodeRecord` has no status; readiness is derived from the validation run of its current manifest revision. See [`docs/architecture/episode-domain.md`](docs/architecture/episode-domain.md).

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
| Robot sandbox (v2) | `ros2/`                   | ROS2 Jazzy Docker environment: streaming bridge node + durable MCAP capture (not an `apps/` service)          |


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

`recording_scene_building`
  build_recording_scenes → register_scenes → validate_scene → profile_scene — one registered RobotRun recording → canonical Scenes (see [Scene domain](docs/architecture/scene-domain.md))

`detection_evaluation`
  predict_detection → evaluate_detection (pinned sample views or ScenarioSet in; pinned prediction and label revisions out)

`scenario_curation`
  mine_scenarios → score_scenario_readiness (pinned sample views in; ScenarioSet revision out)

`aligned_episode_building`
  align_episode → validate_aligned_episode → profile_aligned_episode — one registered Episode → a derived, validated `AlignedEpisode` revision

`recording_episode_building`
  build_recording_episodes → register_episodes → validate_episode → profile_episode

Canonical Scenes and Episodes are built only from registered RobotRun recordings; `register_scenes` / `register_episodes` are the only writers of their membership. See [Scene domain](docs/architecture/scene-domain.md).

#### Standalone robot jobs (v2)

Robot/RobotRun is a separate domain from Dataset/DatasetVersion (see [Core concepts](#robot--robotrun--mission--robotstate-v2)), so these dispatch as plain Jobs, not as part of a named pipeline:

`register_robot_run`
  verifies a published RobotRunManifest + recording → recording/manifest `ArtifactRecord`s + immutable `RobotRun`

`ingest_robot_states`
  reads a RobotRun's registered recording (by `robot_run_id`, via the verified recording resolver) → `RobotState` + `Mission` rows

`export_robot_analytics_snapshot`
  exports one RobotRun's `RobotState`/`Mission` rows to `robot_telemetry.parquet` / `missions.parquet`

### Robot data ingestion path (v2)

```text
robot / dataset replay (tools/dataset-acquisition --replay)         external dataset (batch)
  └─ ROS2 topics (telemetry, camera, lidar, CameraInfo, /tf, /tf_static)   └─ tools/dataset-acquisition → MCAP
       └─ streaming_bridge_node ─► Kafka ─► ros2/capture ─► L1 MCAP ◄────────────┘
            └─ Recording Publisher (DB-free) ─► MCAP + RobotRunManifest (Artifact Store)
                 └─ register_robot_run Job ─► Postgres (ArtifactRecords, RobotRun)
                      └─ resolve_recording(robot_run_id) ─► verified local copy
                           ├─ recording_scene_building / recording_episode_building ─► Scenes / Episodes
                           └─ ingest_robot_states Job ─► Postgres (RobotState, Mission)
                                └─ export_robot_analytics_snapshot Job ─► Parquet ─► DuckDB
```

Standard ROS2 messages (`nav_msgs/Odometry`, `sensor_msgs/Imu`, `sensor_msgs/BatteryState`) are used where they fit; `/vehicle/control` and `/mission/status` have no matching standard message, so they are published as `std_msgs/String` carrying flat JSON — `RosbagAdapter` recognizes the schema and unwraps it, rather than requiring a custom `.msg` colcon package.

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

## Demo 1: derived perception — labels, sample views, curation, detection, evaluation

Real nuScenes data enters only through the acquisition tool. The recording carries no annotation; its labels leave as a separate label document and enter the platform as an independent, pinned label set.

```bash
make local-up
make e2e-perception                     # BACKEND=mock (default)
```

```text
nuScenes scene-0061
  → dataset-acquisition            MCAP (camera, lidar PointCloud2, poses, CAN)   ┐ no labels
  → dataset-acquisition nuscenes-labels   label document (4699 labels, 39 samples)  ┘ separate file
  → RobotRun → recording_scene_building      canonical Scenes (annotation count 0)
  → IMPORT_LABELS                  label set revision (external provenance)
  → BUILD_SCENE_SAMPLE_VIEWS       camera-anchored samples + nearest lidar (≤25 ms) + ego pose (≤5 ms)
  → scenario_curation              ScenarioSet revision pinning the views
  → detection_evaluation           prediction revision → evaluation against the pinned label revision
```

The script asserts that every revision pins what it consumed, that retries converge on the same revisions and ids, that Scenes are untouched by every derived step, and that a real lidar payload decodes (PointCloud2 CDR) to exactly the source `.pcd.bin` points.

---

## Demo 2: real GroundingDINO detection evaluation

`BACKEND=grounding_dino make e2e-perception` runs the same vertical with the real model: boxes are lifted to 3-D through the sample's lidar observation (decoded by its declared media type) and the associated ego pose, and carry the frame they are expressed in.

```bash
make local-up
make inference-local-up   # or make inference-gpu-up for GPU
make e2e-perception BACKEND=grounding_dino
```

This backend path was not re-run in the step that introduced the pinned derived layer; only its unit tests and the mock vertical were.

---

## Demo 3: scenario curation

Curation is explicit. `mine_scenarios` takes pinned sample views and criteria — `require_labels`, label count bounds, `required_channels`, allowed Scene `readiness`, sort and limit — against one pinned label set revision, and writes an immutable ScenarioSet revision whose members pin the views and name the selected samples. `score_scenario_readiness` scores the pinned members. It is part of `make e2e-perception`.

---

## Demo 4: robot data acquisition (batch and streaming)

One logical source, two acquisition modes, one recording contract. `tools/dataset-acquisition` converts a nuScenes scene into acquisition events and either writes an MCAP (batch) or replays the same payload bytes onto ROS2 topics (streaming); the platform's bridge, Kafka and capture turn the topics back into an MCAP. Either recording is published, registered as a `RobotRun`, and built into canonical Scenes and Episodes through the same pipelines.

> Requires nuScenes v1.0-mini with the CAN bus expansion at `data/raw/nuscenes/` (see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md)).

### Quickstart

```bash
make local-up
make streaming-up                   # Kafka
make e2e-batch-acquisition          # batch: acquire → check → publish → register → ingest, via containers + FastAPI
make e2e-streaming-equivalence      # streaming: replay → ROS2 → bridge → Kafka → capture → RobotRun,
                                    # then Scenes + Episodes equivalent to the batch acquisition
```

Both targets need only Docker Compose, curl and jq on the host; platform operations go through FastAPI. Details: [`tools/dataset-acquisition/README.md`](tools/dataset-acquisition/README.md) and [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md).

**Current limitations:**

- Capture → publish → register are explicit steps (no automatic hand-off from a finalized capture)
- `/vehicle/control` and `/mission/status` use a `std_msgs/String` + JSON bridge instead of a proper custom `.msg` package (would need a `colcon` build step)
- No live robot control — this is acquisition of recordings, not real-time command/control (see `docs/adr/005-ros2-vs-kafka-boundary.md`)

See [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md) for the full ingestion flow and current limitations.

---

## Demo 5: canonical Episodes from a robot recording

`recording_episode_building` turns one registered RobotRun into canonical Episodes. The build configuration decides which topics are observation / state / action streams, which fields they keep, which preserved timestamp is canonical, and how Episodes are cut (here: one Episode per recorded `/mission/status` running → completed pair):

```bash
make local-up
make e2e-recording-episode   # nuScenes -> acquisition -> RobotRun -> Episodes -> validation/profile, retry, replacement
```

Example (scene-0061): one Episode `task-mission-scene-0061-000` with 224 front-camera observations, 976 state occurrences (odometry + battery), 38 control actions and 2 mission events, each at its recorded timestamp — nothing resampled or aligned. Inspect it with `GET /api/v1/episodes/{episode_id}/manifest`. See [`docs/architecture/episode-domain.md`](docs/architecture/episode-domain.md).

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
POST /api/v1/robot-runs:register                       # {manifest_uri} -> REGISTER_ROBOT_RUN Job
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
make test                           # infrastructure-independent unit tests
make test-integration               # real Postgres + MinIO tests
make e2e-cleanroom                  # the full-platform acceptance workflow (destructive local-reset + real E2E) -- see [docs/development/local-development.md](docs/development/local-development.md)
```

**Robot learning (v2, optional):** requires the nuScenes CAN bus expansion unzipped at `data/raw/nuscenes/can_bus/` (a separate download from nuScenes mini — see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md)).

```bash
make e2e-recording-episode          # canonical Episodes from a batch-acquired recording
```

The learning chain on canonical Episodes (`make e2e-robot-learning`) is unavailable until ADR-007 implementation step 11.

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
| `make local-reset` | **Destructive** — wipe all local Postgres/Redis/MinIO data, bring up a fresh stack from the same images (no rebuild — run `make compose-build` first if source changed) |
| `make status` / `make logs` | Service status / follow logs                 |
| `make db-migrate`  | Run Alembic upgrade head (also run by `local-up`)      |
| `make canonical-bootstrap` / `make canonical-verify` | **Unavailable until ADR-007 implementation step 11** (the frozen v0.0 baseline was built by a removed pipeline) — see [`docs/development/canonical-baseline.md`](docs/development/canonical-baseline.md) |
| `make streaming-up` / `make streaming-down` | Opt-in local Kafka broker for the streaming transport — never part of `make local-up`; see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md) |


### Development


| Command                     | Description                   |
| --------------------------- | ----------------------------- |
| `make test`                 | worker + api + sceneops-core + sceneops-analytics + inference-server unit tests — no infra needed |
| `make test-integration`     | sceneops-db (real Postgres) + sceneops-storage (real MinIO) — requires `make local-up` |
| `make lint` / `make format` | Ruff check / format           |
| `make worker-imports`       | Validate job registry imports |


### E2E

There is no bare `make e2e` aggregate — Scene/robot-learning/perception/
interop have materially different infrastructure requirements (default stack
/ ROS2 sandbox / inference service / isolated LeRobot venv respectively), so
one aggregate would hide which of those a failure actually needed.
`make e2e-cleanroom` is the only full-platform acceptance entry point; see
[docs/development/local-development.md](docs/development/local-development.md)
for the full surface and what moved to `smoke-*`/`verify-*`/`test-integration`.

**E2E Workflows (primary surface):**

|  Command | Description |
| --- | --- |
| `make e2e-recording-scene [SCENE=scene-0061]` | nuScenes → acquisition container → MCAP → RobotRun → `recording_scene_building` → canonical Scenes → validation/profile, through FastAPI |
| `make e2e-recording-episode [SCENE=scene-0061]` | nuScenes → acquisition container → MCAP → RobotRun → `recording_episode_building` → canonical Episodes → validation/profile, through FastAPI |
| `make e2e-robot-learning` | **Unavailable until ADR-007 implementation step 11** (learning chain on canonical Episodes) |
| `make e2e-interop` | Real Postgres/MinIO → SceneOpsDataset → LeRobot → golden comparison; requires `make lerobot-sync` |
| `make e2e-cleanroom` | **The full-platform acceptance workflow**: `local-reset` → `e2e-recording-scene` → `e2e-recording-episode` → persisted-state validation. **Destructive** (preserves `data/raw`) |

**Secondary E2E:**

|  Command | Description |
| --- | --- |
| `make e2e-scene-analytics-export` | Scene-domain analytical Parquet export over recording-derived Scenes (distinct from `EXPORT_LEARNING_DATA`) |
| `make e2e-lerobot-container` | Containerized variant of `e2e-interop`'s golden round trip |
| `make e2e-streaming-equivalence [SCENE=scene-0061 \| RATE=2]` | nuScenes replay → ROS2 → bridge → real Kafka → capture → `RobotRun` → Scenes/Episodes, proven equivalent to batch acquisition; requires `make local-up` and Kafka — see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md) |
| `make ros2-test` | Bridge + capture unit and real-Kafka integration tests in the ros2 image |
| `make e2e-streaming-capture [SCENE=scene-0061 \| RATE=10.0]` | Real Kafka → durable MCAP capture (run-scoped consumer) → RosbagAdapter compatibility check; requires `make streaming-up`; zero Postgres/MinIO state — see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md) Part 3 |
| `make e2e-robot-run-registration [SCENE=scene-0061 \| RATE=10.0]` | Captured MCAP → ArtifactStore → ArtifactRecord → canonical `RobotRun` (real Postgres/MinIO), plus idempotent-retry/conflict verification; requires `make local-up` and `make streaming-up` |
| `make e2e-robot-run-learning` | **Unavailable until ADR-007 implementation step 11** |
| `make e2e-perception [BACKEND=mock\|grounding_dino]` | Real nuScenes → RobotRun → Scenes → labels → sample views → ScenarioSet → detection → evaluation, every revision pinned |
| `make e2e-episode-alignment` | Real nuScenes → RobotRun → canonical Episode → `AlignedEpisode` → learning export, with retry-convergence checks |

**Smoke** (transport/liveness only — never creates persistent domain data): `make smoke-api`, `make smoke-lerobot-container`, `make smoke-streaming` *(requires `make streaming-up`; zero Postgres/MinIO state — see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md))*.

**Verification** (execution-model/backend-substitution properties, not domain workflows): `make verify-reliability`, `make verify-airflow-backend` *(requires `make airflow-up`; an alternate-orchestrator compatibility check, not general backend substitution)*. Both run `recording_scene_building` on a camera RobotRun they acquire once and then reuse.

**Debug / Stage commands** (individual pipeline stages, for manual debugging — not primary E2E workflows): `make e2e-robot-can-replay SCENE=scene-0061 RATE=10.0`; `make e2e-episode-building` and `make e2e-episode-curation` are unavailable until ADR-007 step 11.


### ROS2 / Robot (v2)


| Command | Description |
| --- | --- |
| `make ros2-up` / `ros2-down` | Start/stop the ROS2 Jazzy sandbox container |
| `make ros2-shell` | Interactive shell in the sandbox (`ros2` CLI, `rclpy` on PATH) |
| `make ros2-check` | Smoke-test `rclpy` import + MCAP storage plugin |
| `make worker-register-robot-run MANIFEST_URI=..` | `REGISTER_ROBOT_RUN` for a RobotRunManifest published by `python -m sceneops_integrations.recording publish` (same registrar as `POST /robot-runs:register`), see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md) §3.2 |


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
│           ├── scenes/             # recording scene builder, registrar, validator, profiler, selection filter
│           ├── episodes/           # recording episode builder, registrar, resolver, validator, profiler
│           ├── recordings/         # payload extraction / identity / publication shared by both builders
│           ├── datasets/ingestion/ # RosbagAdapter (robot-telemetry projection of a recording)
│           ├── evaluation/detection/  # CenterDistanceDetectionEvaluator, accumulator
│           ├── inference/          # mock / ONNX / GroundingDINO + frustum-lift backends
│           ├── stores/robots.py    # RobotStore (v2)
│           ├── cli/robots.py       # `sceneops-worker robots register --manifest-uri` (v2)
│           ├── core/               # WorkerContext, stores, DI
│           ├── execution/          # Celery app factory, job dispatcher
│           └── tests/              # unit tests
├── packages/
│   ├── sceneops-core/              # domain schemas, enums, pipeline definitions (incl. episodes/, robots/ — v2)
│   ├── sceneops-db/                # SQLAlchemy models, async repositories, Alembic
│   ├── sceneops-storage/           # LocalArtifactStore, S3ArtifactStore
│   └── sceneops-analytics/         # Parquet table builders, DuckDB query helper (v2 adds robot tables)
├── tools/                          # isolated uv projects, outside the workspace:
│                                   #   dataset-acquisition (external dataset → L1 MCAP; no SceneOps deps),
│                                   #   lerobot-integration
├── ros2/                           # (v2) ROS2 Jazzy Docker sandbox
│   ├── Dockerfile                  #   rclpy, rosbag2, MCAP storage plugin, nuscenes-devkit
│   ├── nodes/streaming_bridge_node.py  #   ROS2 topics → Kafka
│   └── capture/                    #   Kafka → L1 MCAP
├── migrations/                     # Alembic versions
├── scripts/
│   ├── e2e/                        # E2E scripts (incl. e2e_batch_acquisition.sh, e2e_streaming_equivalence.sh — v2)
│   ├── fixtures/                   # dataset registration
│   └── debug/                      # pipeline/job inspection
├── docs/
│   ├── architecture/               # overview, data-model, scene-domain, episode-domain (v2),
│   │                                #   jobs-and-pipelines, storage-layout, quality-and-runs, reserved-and-limitations
│   ├── workflows/                  # robot-run-and-mcap.md (v2)
│   ├── development/                # local-development.md
│   └── adr/                        # architecture decision records
├── compose.yaml                    # root Compose entrypoint (include: compose/*.yaml)
├── compose/                        # core, workers, inference, airflow, ros2, tools
├── Makefile
└── pyproject.toml                  # uv workspace (Python 3.11–3.12)
```

---

## Limitations and roadmap

See [`docs/architecture/reserved-and-limitations.md`](docs/architecture/reserved-and-limitations.md) for the full, code-verified list, including intentionally reserved architecture (unimplemented-but-retained JobTypes, `JOB_STEP_DEFINITIONS_BY_TYPE`) that looks unwired but isn't dead code.

### Current limitations

* The default local dataset is nuScenes mini.
* **(v2)** Episode selection works on aligned revisions rather than raw Episodes: `learning_*.parquet` + `EpisodeCurationManifest`; see [Robot learning data layer](docs/architecture/robot-learning-data.md) and, for the scaled production physical layout, [Scalable learning data](docs/architecture/scalable-learning-data.md).)
* The platform is local-first and optimized for architecture validation, not large-scale production throughput.
* GroundingDINO evaluation results are integration signals, not production model benchmarks.
* Scenario curation is implemented but still marked `experimental=True`.
* Scenario candidates are artifact-backed; there is no per-scenario item table yet.
* Scenario readiness scoring uses label counts, channels and Scene readiness, not image/LiDAR content.
* Sample views associate by nearest / previous only (no pose interpolation); evaluation applies no frame transform between a prediction and a label.
* Scene reconstruction, auto-labeling, and generated dataset preparation pipelines are defined but not implemented.
* Operations and leaderboard APIs exist, but there is no dedicated web UI yet.
* The Airflow pipeline backend is a per-task DAG PoC hardcoded to `recording_scene_building`; other pipeline types still only run through Celery.
* **(v2)** Binary sensor payloads (`sensor_msgs/Image`, `PointCloud2`) decode via CDR but aren't written to files yet — no real camera/LiDAR-publishing ROS2 node exists to test against.
* **(v2)** `/vehicle/control` and `/mission/status` use a `std_msgs/String` + JSON bridge, not a proper custom `.msg` package (would need a `colcon` build step).
* **(v2)** Streamed telemetry does feed MCAP and canonical `RobotRun` registration now (Kafka transport → ROS2 bridge → durable MCAP capture, single-run or continuous multi-run → Recording Publisher → `REGISTER_ROBOT_RUN` → recording Scene / Episode building), but there is still no live robot control, no automatic trigger from a finalized capture into registration (publication and registration are explicit steps), and no process-restart or Kafka-rebalance recovery for continuous multi-run capture; see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md) and `docs/adr/005-ros2-vs-kafka-boundary.md` for the current contract.
* **(v2)** DuckDB queries only work against locally-downloaded Parquet files; querying S3/MinIO-backed artifacts directly would need DuckDB's httpfs/S3 extension, which isn't wired up.

### Roadmap

* Generalize the Airflow per-task DAG PoC beyond `recording_scene_building` to other pipeline types.
* Evaluation-aware scenario mining using FP/FN and per-scene metric signals.
* Pseudo-label candidate workflow for no-GT or weakly labeled scenes.
* VLM-based semantic scene tagging.
* Scene reconstruction package export.
* Auto-labeling pipeline with a labeler registry.
* Per-scenario item table for queryable scenario candidates and review status.
* Web UI on top of existing operations and leaderboard APIs.
* Cloud object storage hardening, including stronger artifact lifecycle and integrity checks.
* **(v2)** A real custom ROS2 `.msg` package for `/vehicle/control` and `/mission/status`, replacing the JSON-over-`std_msgs/String` bridge.
* **(v2)** Automatic triggering of `RobotRun` registration from a finalized streamed capture (today explicit publish + `POST /robot-runs:register` steps), process-restart and Kafka-rebalance recovery for continuous multi-run capture, and eventual live robot control as its own service — not `apps/worker`. The Kafka telemetry transport, ROS2 → Kafka streaming bridge, durable MCAP capture (single-run and continuous multi-run, with capture-session lifecycle), and canonical `RobotRun` registration are all done — see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md).
* **(v2)** Scale-testing with synthetic multi-robot telemetry (N virtual robots, robot-fleet/mission-ingestion throughput — unrelated to the completed learning-data "Phase 5" scaling work below) to compare local (Polars/DuckDB) vs. distributed (Spark) processing for that ingestion path specifically. (This is a distinct, still-open roadmap item, not to be confused with the learning-data storage/access "Phase 5" work, which already measured its own single-node-vs-distributed boundary and concluded Spark is not currently justified there — see [Scalable learning data](docs/architecture/scalable-learning-data.md) §10.)

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
