# SceneOps Platform

SceneOps Platform is a local-first robotics data and MLOps platform. A robot's (or an external dataset's) sensor data enters as an immutable raw recording, is canonicalized into source-faithful Scenes and Episodes, and feeds derived workflows — labels, sample views, ScenarioSets, detection evaluation, and learning-data export — where every derived result pins the exact revisions it consumed.

It uses nuScenes mini as a realistic autonomous-driving fixture and implements production-shaped components: a FastAPI control plane, PostgreSQL metadata, object-storage artifacts, Celery job execution, and durable pipeline/job orchestration.

> Modern robotics AI systems need more than model inference: reliable sensor-data ingestion, scene-level quality control, reproducible evaluation, artifact lineage, and data-selection workflows. SceneOps explores that problem as a small but production-shaped platform.

Start at [`docs/architecture/overview.md`](docs/architecture/overview.md) for the full documentation set; [ADR-007](docs/adr/007-canonical-ingestion-architecture.md) and [ADR-009](docs/adr/009-job-centric-execution-and-durable-boundaries.md) are the decision records for the ingestion, workflow and execution architecture described below.

---

## Architecture

```text
L0  transport       ROS 2 topics -> Kafka -> capture            (non-canonical, bounded replay)
L1  RobotRun        immutable recording (MCAP) + RobotRunManifest, registered and verified
L2  Scene / Episode canonical, source-faithful units built from a RobotRun; checksum-pinned manifests
L3  derived         labels, sample views, ScenarioSets, predictions, evaluations,
                    aligned episodes, learning exports -- immutable revisions that pin their inputs
```

```text
Acquisition
→ RobotRun

RobotRun
├→ Scene
└→ Episode

Scene
→ Labels / SampleView / ScenarioSet
→ Inference / Evaluation

Episode
→ AlignedEpisode
→ LearningDataExport
```

Platform primitives are generic; domain semantics stay explicit. PostgreSQL holds identity, metadata, membership and lineage references; object storage holds durable artifacts; Parquet is the analytical representation; Redis is transient execution support; Kafka is streaming transport, never canonical storage.

| Layer            | Location                      | Role                                                                             |
| ---------------- | ----------------------------- | -------------------------------------------------------------------------------- |
| Control plane    | `apps/api`                    | REST API: datasets, scenes, episodes, scenarios, jobs, pipelines, runs, artifacts, robots |
| Execution        | `apps/worker`                 | Pipeline orchestration, job execution, artifact writes, `sceneops-worker` CLI (recovery, acquisition commands) |
| Acquisition      | `apps/streaming-bridge`, `apps/capture`, `apps/publisher` | ROS 2 → Kafka bridge; Kafka → MCAP capture; MCAP → published RobotRun objects |
| Inference server | `apps/inference-server`       | Optional GroundingDINO server                                                    |
| Foundations      | `packages/sceneops-{core,db,storage,streaming}` | Domain schemas and contracts; PostgreSQL; ArtifactStore; Kafka transport |
| Capabilities     | `packages/sceneops-{recording,execution,acquisition,scenes,episodes,derived,inference,evaluation,analytics}` | Capture and publication; Job / Pipeline execution; acquisition lifecycle; Scene and Episode building; derived storage; inference; evaluation; Parquet analytics |
| Tools            | `tools/`                      | Checks, E2E journeys, baselines, benchmarks, dev helpers; isolated uv projects `dataset-acquisition` (external dataset → MCAP) and `lerobot-integration` |

Apps are processes; packages are the reusable code they import; tools are never imported
by either. Details and the dependency rules: [Repository structure](docs/architecture/repository-structure.md).

### Pipelines and jobs

A Pipeline exists only where multi-stage orchestration, retry and lineage justify it. SceneOps has exactly four:

| Pipeline                         | Stages                                                                                              |
| -------------------------------- | --------------------------------------------------------------------------------------------------- |
| `recording_scene_building`       | RobotRun → `build_recording_scenes` → `register_scenes` → `validate_scene` → `profile_scene`        |
| `recording_episode_building`     | RobotRun → `build_recording_episodes` → `register_episodes` → `validate_episode` → `profile_episode` |
| `scene_ml_evaluation`            | pinned label sets + policy → `build_scene_sample_views` → `mine_scenarios` → `score_scenario_readiness` → `predict_detection` → `evaluate_detection` |
| `episode_learning_data_building` | pinned Episodes + config → `align_episode` → `export_learning_data`                                  |

Single operations are atomic Jobs (`POST /jobs`): `register_robot_run`, `import_labels`, `build_scene_sample_views`, `mine_scenarios`, `predict_detection`, `evaluate_detection`, `align_episode`, `validate_aligned_episode`, `profile_aligned_episode`, `export_learning_data`, `curate_episodes`, and the robot-telemetry and analytics-snapshot jobs. A job that is a pipeline stage is the same handler as the atomic job. Details: [`docs/architecture/jobs-and-pipelines.md`](docs/architecture/jobs-and-pipelines.md).

```text
PipelineRun → PipelineTaskRun → durable Job → asynchronous dispatch → Celery worker → JobRunner → domain handler
```

### Execution model

Celery + Redis is the only execution backend. A Pipeline is durable state: the `PipelineOrchestrator` advances it one short step at a time and never runs a handler; every task is a durable Job that `JobRunner` — the sole runtime entry point for Jobs — executes on a job worker. Execution identity is `execution_key` (idempotent create, at most one in-flight Job per key; `force` never reuses a succeeded record), a redispatch resumes at the first failed or blocked task, and a task can block its pipeline through a quality gate. Task results are normalized into `outputs` (downstream refs), `metrics`, `lineage.artifacts`, and per-task `summary` / `rawResult`.

A successful mutating API response means PostgreSQL has committed; operations that dispatch work commit first and send second. Derived artifacts are immutable and content-pinned: bytes are written once at a checksum-qualified key, so identical retries converge and changed content becomes a new revision. See [Jobs and pipelines](docs/architecture/jobs-and-pipelines.md) and [Storage layout](docs/architecture/storage-layout.md).

---

## Core concepts

- **RobotRun** — immutable provenance of one finalized, published and verified recording; created only by `REGISTER_ROBOT_RUN` from a manifest written by the database-free Recording Publisher. `Robot`, `Mission` and `RobotState` are its registry entry and derived telemetry projection.
- **Scene** — canonical unit of sensor data: every observation of its channels, each on its own clock, plus calibration and poses. No labels, no annotations, no synchronization. `SceneRecord` is its catalog row and pins one manifest revision. See [Scene domain](docs/architecture/scene-domain.md).
- **Episode** — canonical, task-oriented projection of one RobotRun: observation, state, action and event streams kept asynchronous. A sibling of Scene over the same RobotRun; temporal alignment is a separate derived step. See [Episode domain](docs/architecture/episode-domain.md).
- **LabelSet** — post-acquisition labels anchored on canonical observations, with the coverage they annotated; imported with `IMPORT_LABELS`, never written into a Scene.
- **SceneSampleView** — a derived, policy-driven synchronization of one Scene: which channel defines the sample instants, which others and which ego pose are associated (nearest / previous, with a tolerance, never across clocks), which label revisions attach.
- **ScenarioSet** — a curated selection over pinned sample views with explicit criteria; an immutable revision.
- **Prediction / evaluation** — derived results that pin the prediction, label and view revisions they consumed; a prediction and a label in different frames fail instead of being compared.
- **AlignedEpisode / LearningDataExport** — a derived alignment of a pinned Episode revision on an explicit timeline, and a sharded Parquet export of pinned aligned revisions (LeRobot is a downstream output of an export). See [Robot learning data](docs/architecture/robot-learning-data.md).
- **DatasetVersion** — canonical membership and scope, with registrar-owned summaries. It holds no channel requirements, no source location and no free-form metadata.

Every derived object is an immutable, checksum-pinned revision: it records the exact revisions it was built from, and a consumer resolves a pin, never "the latest". See [Derived layer](docs/architecture/derived-layer.md).

---

## Quickstart

**Requirements:** Docker + Docker Compose, [uv](https://github.com/astral-sh/uv), Python 3.11–3.12, nuScenes mini (with the CAN bus expansion) at `./data/raw/nuscenes`.

```bash
cp .env.example .env.local          # configure storage backend, DB, Redis
make setup                          # install deps + pre-commit hooks
make local-up                       # idempotent: Postgres + Redis + MinIO + migrate + API + workers
make test                           # unit suites, no infrastructure
make reference-data-bootstrap       # source preparation: nuScenes mini -> locked MCAPs, verified (once per scope)
make reference-contract-bootstrap   # converge on the golden reference contract: 10 fixtures x both modes = 20 RobotRuns (reuses what exists)
make reference-contract-verify      # read-only: exactly the contract's RobotRuns, Scenes and Episodes
make e2e-scene-ml                   # Scenes -> labels -> views -> ScenarioSet -> prediction -> evaluation
make e2e-episode-learning           # Episodes -> aligned -> learning export -> LeRobot round trip
make local-reset                    # [destructive] drop all generated runtime state; the reference environment is then rebuilt from the locked corpus
```

Artifact storage backend (`.env.local`):

```bash
SCENEOPS_WORKER_ARTIFACT__BACKEND=local                              # local filesystem
SCENEOPS_WORKER_ARTIFACT__BACKEND=minio                              # MinIO / S3
SCENEOPS_WORKER_ARTIFACT__ROOT_URI=s3://sceneops
SCENEOPS_WORKER_ARTIFACT__ENDPOINT_URL=http://minio:9000
```

See [`docs/development/local-development.md`](docs/development/local-development.md) for the bootstrap / reset / env-file model and the test and journey commands.

---

## Common commands

`make help` lists every supported command; `make check-commands` verifies that the surface is consistent.

### Stack

| Command | Description |
| --- | --- |
| `make local-up` | Idempotent bootstrap: infra → health → MinIO buckets → migrate → API + workers |
| `make local-down` | Stop services, **preserve** Postgres/Redis/MinIO data |
| `make local-reset` | **Destructive** — wipe all local Postgres/Redis/MinIO data, bring up a fresh stack from the same images (run `make compose-build` first if source changed) |
| `make status` / `make logs` | Service status / follow logs |
| `make db-migrate` | Alembic upgrade head (also run by `local-up`) |
| `make streaming-up` / `make streaming-down` | Opt-in local Kafka broker for the streaming transport; see [`docs/architecture/streaming-transport.md`](docs/architecture/streaming-transport.md) |
| `make recovery-up` / `make recovery-down` / `make recovery-logs` | Opt-in polling loops that publish finalized captures, register published recordings (`publish-pending`, `reconcile --once --apply`; see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md) §3.2) and recover lost workers and lost messages (`sceneops-worker recover`; see [`docs/architecture/jobs-and-pipelines.md`](docs/architecture/jobs-and-pipelines.md) §5) |
| `make reconcile-once` / `make reconcile-apply` | One acquisition reconciliation pass: observe only / bounded registration recovery |
| `make acquisition-status` / `make artifact-lifecycle-once` | Read-only reports: derived per-run acquisition status with operational aggregates / classification of `robot_runs/` objects (nothing is stored or deleted) |

### Validation

The whole acceptance surface is the commands below; [`docs/development/test-matrix.md`](docs/development/test-matrix.md) says what each proves and which state it may leave behind.

| Command | Description |
| --- | --- |
| `make test` | Dependency-direction check, then the unit suites of every app, package and tool (the ROS 2 suites run in `make streaming-test`) — no infrastructure |
| `make test-integration` | Real Postgres + MinIO: sceneops-db, sceneops-storage, every `*_integration.py` module (registrars, recording Scene / Episode and derived verticals, selective Parquet reads) — in a disposable database and bucket, needs `make local-up`; a skipped test fails the run |
| `make test-infrastructure [SUITE=…]` | Real-infrastructure suites; `pipelines` and `recovery` fail instead of skipping. `pipelines` (default): pipeline contracts on a disposable execution runtime (own API, workers, Redis, database and bucket). `recovery`: acquisition recovery under injected faults and the full capture → RobotRun lifecycle acceptance. `kafka`: ROS 2 bridge + capture tests and the transport smoke (needs `make streaming-up`). `boundaries`: acquisition tool and LeRobot adapter in their own uv projects, acquisition images, raw-source mount of the runtime services |
| `make reference-contract-verify` | Read-only: exactly the golden contract's 20 RobotRuns, Scenes and Episodes; reports non-contract state (`REQUIRE_PRISTINE=1` fails on it) |
| `make e2e-streaming-equivalence` | Read-only: the contract's Recording Import and Streaming Acquisition RobotRuns of one fixture are equivalent (see below) |
| `make e2e-scene-ml` / `make e2e-episode-learning` | The two derived journeys on the golden RobotRun, into fixed test-owned Datasets (see below) |
| `make e2e-cleanroom` | The acceptance of reconstruction. **Destructive** (see below) |

`make lint` / `make format` run Ruff; `make check-commands` verifies that the command surface is consistent. Operator diagnostics (`check-*`, `disk-report`, `bridge-check`, …) are manual tools, not acceptance gates, and the scripts under [`tools/benchmarks/`](tools/benchmarks/README.md) are measurement tooling that no command runs.

### Reference environment and E2E journeys

There are four E2E journeys. Platform operations go through FastAPI and bulk data through one-shot containers; the host needs only Docker Compose, curl and jq. There is no bare `make e2e` aggregate — the journeys need different infrastructure.

| Command | Journey |
| --- | --- |
| `make reference-contract-bootstrap` / `make reference-contract-verify` | Developer orchestration, not a pipeline: the golden reference contract — each corpus fixture ingested once by Recording Import and once by Streaming Acquisition under a fixed identity (20 RobotRuns, 20 whole-recording Scenes, 20 Episodes); the bootstrap composes the two baseline bootstraps, the verifier is read-only and reports contract vs non-contract RobotRuns. See [`docs/development/reference-contract.md`](docs/development/reference-contract.md) and [`docs/development/canonical-baseline.md`](docs/development/canonical-baseline.md) |
| `make e2e-streaming-equivalence` | Read-only: the reference contract's Recording Import and Streaming Acquisition RobotRuns of one fixture, read from the ArtifactStore, are semantically equivalent in acquisition and in canonical Scenes and Episodes (creates no state; needs neither Kafka nor ROS 2) |
| `make e2e-scene-ml` | Scenes → labels → sample views → ScenarioSet → prediction → evaluation (mock backend), into the fixed Dataset `sceneops-test-scene-ml` |
| `make e2e-episode-learning` | Episodes → AlignedEpisodes → learning export → export verification + LeRobot round trip, into the fixed Dataset `sceneops-test-episode-learning` |
| `make e2e-cleanroom` | **The acceptance of reconstruction**: reset the generated runtime → rebuild the golden contract from the preserved reference inputs (verified pristine, then a second bootstrap that converges) → `e2e-scene-ml` and `e2e-episode-learning` on one fixture → the contract is still valid and unchanged. **Destructive** (preserves `data/raw`, `data/reference`, `config/reference`) |
| `make acceptance-grounding-dino` | Opt-in model-backend acceptance of `e2e-scene-ml` with the GroundingDINO backend (needs an inference server) |

### Streaming and robot data

| Command | Description |
| --- | --- |
| `make streaming-test` / `make bridge-shell` / `make bridge-check` | Bridge and capture tests in their ROS 2 images (needs `make streaming-up`) / shell in the bridge image / `rclpy` check |
| `POST /api/v1/robot-runs:register` | `REGISTER_ROBOT_RUN` for a RobotRunManifest published by `python -m sceneops_publisher publish` (`reconcile --apply` submits it for unregistered manifests); see [`docs/workflows/robot-run-and-mcap.md`](docs/workflows/robot-run-and-mcap.md) |

---

## Documentation

| Tier | Where | Answers |
| --- | --- | --- |
| README | this file | what SceneOps is and how to start |
| Architecture | [`docs/architecture/`](docs/architecture/overview.md) | how the system works now (current implementation only) |
| Workflows, development | [`docs/workflows/`](docs/workflows/robot-run-and-mcap.md), [`docs/development/`](docs/development/local-development.md) | data flows, commands and the test surface |
| ADRs | [`docs/adr/`](docs/adr/) | why a decision was made (history of decisions) |
| History | [`docs/history/`](docs/history/) | point-in-time studies and benchmarks; not current truth |

No roadmap is maintained in the repository.

---

## API overview

All API routes are under `/api/v1`. Health is exposed at the root.

| Surface          | Areas                                                  | Purpose                                                      |
| ---------------- | ------------------------------------------------------ | ------------------------------------------------------------ |
| Data catalog     | Datasets, Scenes, Episodes, Scenarios, Models          | Register and inspect data and model resources                |
| Robots           | Robots, RobotRuns, Missions, RobotStates               | Register robots / runs; query telemetry and mission history  |
| Execution        | Pipelines, Jobs, Executions                            | Create, run and monitor asynchronous workflows               |
| Runs & artifacts | Inference, Evaluations, Artifacts                      | Track model runs, metrics, outputs and artifact lineage      |
| Operations       | Operations, Leaderboards                               | Summaries, failures, timelines and rankings                  |

Representative routes:

```text
GET  /health
GET  /api/v1/datasets/{id}/versions/{v}/quality
GET  /api/v1/scenes/{scene_id}/quality
GET  /api/v1/episodes                                  # filter by dataset_id/robot_run_id/mission_id
GET  /api/v1/episodes/{episode_id}/manifest
POST /api/v1/robot-runs:register                       # {manifest_uri} -> REGISTER_ROBOT_RUN Job
POST /api/v1/pipelines/runs                            # the four pipeline types
POST /api/v1/pipelines/runs/{id}/execute
GET  /api/v1/pipelines/definitions
POST /api/v1/jobs                                      # atomic jobs
GET  /api/v1/inference/runs/{id}
GET  /api/v1/evaluations/runs/{id}/metrics
GET  /api/v1/artifacts?kind=...&owner_type=...&owner_id=...
GET  /api/v1/operations/summary
```

Explore all registered routes:

```bash
curl http://localhost:8000/openapi.json | jq '.paths | keys[]'
```

---

## Repository structure

```text
sceneops-platform/
├── apps/                           # processes (one container image each)
│   ├── api/                        # FastAPI control plane (platform/, domains/, views/)
│   ├── worker/                     # Celery workers + `sceneops-worker` CLI: JobRunner, PipelineOrchestrator, handlers
│   ├── capture/                    # Kafka -> MCAP + capture receipt, one process per run
│   ├── streaming-bridge/           # ROS 2 topics -> Kafka (integration adapter)
│   ├── publisher/                  # database-free: finalized MCAP -> published RobotRun objects
│   └── inference-server/           # GroundingDINO server (port 8001; optional)
├── packages/                       # reusable production code; never imports apps/ or tools/
│   ├── sceneops-core/              # domain schemas, enums, pipeline and job definitions (no I/O)
│   ├── sceneops-db/                # SQLAlchemy models, repositories, sessions
│   ├── sceneops-storage/           # LocalArtifactStore, S3ArtifactStore, write-once primitive
│   ├── sceneops-streaming/         # TelemetryEnvelope, channel registry, Kafka producer / consumer
│   ├── sceneops-recording/         # capture, publication, conformance, recording reader, receipts
│   ├── sceneops-execution/         # Job / Pipeline services, leases, execution recovery, dispatch
│   ├── sceneops-acquisition/       # reconciliation, acquisition status, artifact lifecycle
│   ├── sceneops-scenes/            # Scene building, validation, profiling, artifact layout
│   ├── sceneops-episodes/          # Episode building, validation, profiling, artifact layout
│   ├── sceneops-derived/           # write-once derived manifests and run artifacts
│   ├── sceneops-inference/         # detection inference backends
│   ├── sceneops-evaluation/        # detection evaluation
│   └── sceneops-analytics/         # Parquet tables, learning-data reader, external adapters
├── tools/                          # developer / CI / benchmark utilities; not production
│   ├── checks/ e2e/ baselines/ reference/ benchmarks/ dev/
│   └── dataset-acquisition/, lerobot-integration/   # isolated uv projects
├── tests/                          # cross-system tests: infrastructure/ (PostgreSQL, MinIO, Redis, Celery), streaming/ (ROS 2 + Kafka)
├── config/                         # channels/, baselines/, reference/
├── migrations/                     # Alembic versions
├── docs/                           # architecture/, workflows/, development/, adr/, history/
├── compose.yaml, compose/          # Compose entrypoint and core, workers, inference, tools,
│                                   #   streaming, acquisition, recovery, lerobot, test-runtime
├── Makefile, makefiles/
└── pyproject.toml                  # uv workspace (Python 3.11–3.12)
```

---

## Limitations

The code-verified list is in [`docs/architecture/limitations.md`](docs/architecture/limitations.md). Current constraints:

* The default fixture is nuScenes mini; the journeys are validated on one scene (`scene-0061`).
* The platform is local-first and optimized for architecture validation, not large-scale throughput.
* GroundingDINO results are integration signals, not production model benchmarks; the default Scene ML journey uses the mock backend.
* Scenario candidates are manifest-backed (no per-scenario table); readiness scoring uses label counts, channels and Scene readiness, not image / LiDAR content.
* Sample views associate by nearest / previous only (no pose interpolation); evaluation applies no frame transform between a prediction and a label.
* Episodes have no label sets; learning export is numeric scalar / vector only.
* Streamed capture reaches a canonical `RobotRun` through recoverable one-shot commands (`publish-pending`, `reconcile --once --apply`; `make recovery-up` loops them locally), not through a trigger in capture itself; nothing supervises capture, and there is no live robot control.
* A Job whose worker died, or work whose Celery message was lost, is recovered only while execution recovery runs (`make recovery-up`), and pipeline tasks run strictly serially; capture replays the whole Kafka topic; artifacts are never deleted by the platform.
* DuckDB queries only work against locally downloaded Parquet files.
* Operations and leaderboard APIs exist, but there is no web UI.

---

## Tech stack

|                      |                                                       |
| -------------------- | ----------------------------------------------------- |
| API                  | FastAPI, Pydantic v2, Uvicorn                         |
| Task queue           | Celery, Redis 7                                       |
| Database             | PostgreSQL 16, SQLAlchemy 2.0 async, Alembic          |
| Artifact storage     | MinIO (S3 API), boto3                                 |
| Analytics            | Polars, PyArrow, DuckDB                               |
| Inference (optional) | GroundingDINO, HuggingFace Transformers               |
| Robot data           | ROS 2 Jazzy, rclpy, MCAP (`mcap`, `mcap-ros2-support`), Kafka |
| Dataset fixture      | nuScenes mini (read by the acquisition tool only)     |
| Package manager      | uv workspace                                          |
| Code quality         | Ruff, pre-commit                                      |
| Infra                | Docker Compose                                        |
