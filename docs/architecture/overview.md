# SceneOps Architecture

> Describes the platform as it exists today on `feat/episode-domain` (post
> Stabilization Requests 1–5). This is a "what's actually built" document,
> not a roadmap — see [Reserved architecture and current limitations](./reserved-and-limitations.md)
> for what's intentionally deferred.

## 1. System overview

SceneOps is three independently deployable layers, connected through shared
libraries under `packages/`:

```text
Control Plane (apps/api)
        |
        v
Execution (apps/worker, apps/inference-server)
        |
        v
Storage (PostgreSQL + ArtifactStore)
```

```text
packages/
  sceneops-core       domain schemas, Protocol contracts, pipeline/job definitions
                       — pure Python, no I/O
  sceneops-db         SQLAlchemy models, repositories, converters, Alembic migrations
  sceneops-storage    ArtifactStore implementations (Local/S3)
  sceneops-analytics  Parquet table builders + DuckDB query helper, plus the
                       native learning-data read/access layer
                       (SceneOpsDataset/SequenceSampler/consumer adapters)
```

`sceneops-core` never touches a database or object store directly — it only
defines the `ArtifactStore` Protocol and Pydantic schemas. `sceneops-db` and
`sceneops-storage` provide the real implementations; API and worker inject
them. This is a port/adapter split, and it's why swapping storage backends
(local filesystem <-> MinIO/S3) needs no call-site changes — see
[ADR-002](../adr/002-object-storage-for-assets.md).

## 2. Control plane — `apps/api`

FastAPI application, split into resource CRUD/query APIs and dispatch APIs
that enqueue execution.

```text
apps/api/app/
  domains/            resource-centric domain APIs
    datasets/         Dataset, DatasetVersion, quality
    scenes/           SceneRecord, quality
    episodes/         EpisodeRecord, quality
    scenarios/        ScenarioSet
    robots/           Robot, RobotRun, Mission, RobotState
    inference/        PredictionRun (InferenceRun)
    evaluations/       EvaluationRun
    labels/             labeling
    models/             model registry
  platform/           execution infrastructure API
    jobs/             Job create/query/dispatch
    pipelines/        PipelineRun create/query/dispatch, built-in pipeline definitions
    executions/       dispatch facade routing Job/Pipeline to a real backend (Celery/Airflow)
    artifacts/        artifact metadata query
  views/              aggregate/cross-domain APIs
    leaderboards/     model comparison leaderboards
    operations/       operator-facing dashboards
```

Each domain is layered `router.py` -> `service.py` -> (`sceneops-db`
repository). `dependencies.py` wires session + repository via FastAPI DI.

### Dispatch flow

The API never executes anything itself. `JobDispatchFacade` /
`PipelineDispatchFacade` (`apps/api/app/platform/jobs/dispatch_facade.py`,
`.../pipelines/dispatch_facade.py`) always: 1) commit the record as `QUEUED`
in Postgres, 2) send it to the execution backend via `ExecutionService`.

Commit happens before dispatch deliberately: if the worker races ahead and
already moves the record to `RUNNING`/`SUCCEEDED`, a late `QUEUED` commit
from the API must not overwrite that. If dispatch itself fails, the record
stays `QUEUED` and can be redispatched.

`ExecutionService` already supports choosing a backend per job/pipeline
(`apps/api/app/platform/executions/dependencies.py`'s
`get_pipeline_execution_backend` reads `settings.execution.pipeline_backend`
to pick `CeleryPipelineExecutionBackend` or `AirflowPipelineExecutionBackend`).
What's actually implemented today:

```text
Job      -> Celery only (job_backend is fixed to celery)
Pipeline -> Celery or Airflow (switchable via pipeline_backend)
```

When a pipeline is sent to Airflow, `AirflowPipelineExecutionBackend.dispatch_pipeline`
calls the Airflow REST API (`POST /api/v1/dags/{dag_id}/dagRuns`), setting
`dag_run_id` equal to SceneOps' own `pipeline_run_id` for 1:1 traceability.
Both backends write the same `ExecutionRecord` shape (distinguished by
`execution_backend`), so query paths don't care which backend ran a given
execution.

## 3. Execution — `apps/worker`, `apps/inference-server`

### Worker

Celery-based. Exactly two task types exist (`sceneops_worker/tasks/`):

```text
run_job_task(job_id)               -> JobRunner.run(job_id)
run_pipeline_task(pipeline_run_id) -> PipelineRunner.run(pipeline_run_id)
```

- `JobRunner` (`sceneops_worker/jobs/runner.py`): claims a single job ->
  transitions it to running -> runs the registered handler -> records the
  result. Handlers are registered per `JobType` in `JobHandlerRegistry` —
  see [Jobs and pipelines](./jobs-and-pipelines.md) for the full list.
- `PipelineRunner` (`sceneops_worker/pipelines/runner.py`): runs a
  `PipelineDefinition`'s tasks **sequentially** in one process. If a task is
  judged `BLOCKED` by a quality gate, the whole pipeline ends `BLOCKED`.

Idempotency (`execution_key`) and partial retry (an already-`SUCCEEDED` task
is skipped on redispatch) are both implemented — see
[Jobs and pipelines](./jobs-and-pipelines.md) and
[ADR-004](../adr/004-airflow-vs-celery.md).

### Airflow (pipeline-only, proof of concept)

When `pipeline_backend=airflow`, `PipelineRunner.run()` does not run the
whole pipeline in one process. Instead, each task in the Airflow DAG
(`airflow/dags/sceneops_pipeline_run.py`) runs in its own `DockerOperator`
container (the existing worker image, `apps/worker/Dockerfile`), invoking
`sceneops-worker run-pipeline-task --task-id <id>` — which calls
`PipelineTaskRunner.run()` directly. Quality-gate evaluation and per-task
state recording are exactly the same code path as Celery; only the process
boundary differs.

```text
start -> ingest_scenes -> register_scene -> validate_scene
      -> profile_scene -> build_scene_index -> build_dataset_manifest -> finalize
```

Pipeline-level status transitions (`RUNNING`/`SUCCEEDED`/`BLOCKED`/`FAILED`),
which used to only happen inside `PipelineRunner.run()`'s sequential loop,
are handled by two new entry points that reuse the same private
state-transition methods (`_start_pipeline`/`_succeed_pipeline`/
`_block_pipeline`/`_fail_pipeline`):

```text
start()     : validate the run is executable, then status=RUNNING
finalize()  : read back all task-run statuses, resolve final status as
              BLOCKED > FAILED > SUCCEEDED (trigger_rule=all_done, so it
              always runs regardless of upstream outcome)
```

Current scope is one pipeline type only — `dataset_scene_ingestion` has a
hardcoded DAG task chain. Other pipeline types still only run through
Celery. Extending the Airflow path to other pipelines means generalizing the
DAG (or adding one per type) — out of scope for this PoC.

### Inference server

`apps/inference-server` is a separate FastAPI process that only does
GroundingDINO (torch/transformers) inference. The worker's
`predict_detection` job calls it over HTTP. This keeps heavy ML dependencies
out of the worker process.

## 4. Storage

```text
                 SceneOps
                    |
      +-------------+-------------+
      v                           v
 PostgreSQL                 ArtifactStore
 (sceneops-db)              (sceneops-storage)

 operational / relational   binary / JSON artifacts
 - datasets, scenes,        - scene/episode manifests
   episodes                 - dataset manifests
 - pipeline/job runs        - prediction/evaluation outputs
 - prediction/eval runs     - validation/profile reports
 - artifact metadata        - Parquet analytics tables
```

- PostgreSQL is the system of record for every entity's status, metadata,
  and statistics. It never stores the real payload (manifest, report,
  prediction result) — only the ArtifactStore URI (the `*_uri` columns).
- ArtifactStore has two implementations, `LocalArtifactStore` and
  `S3ArtifactStore` (MinIO included), chosen by `create_artifact_store(settings)`
  from an `ArtifactBackend` setting. See
  [Storage layout](./storage-layout.md) for the full URI structure.

See [Data model](./data-model.md) for entity-level detail, and the
domain-specific docs below for Scene/Episode flow.

## 5. Documentation map

| Topic | Doc |
| --- | --- |
| Entities and relationships | [data-model.md](./data-model.md) |
| Scene domain (build -> quality -> API) | [scene-domain.md](./scene-domain.md) |
| Episode domain (build -> quality -> API) | [episode-domain.md](./episode-domain.md) |
| Robot learning data layer (alignment -> curation -> native dataset -> consumer adapters) | [robot-learning-data.md](./robot-learning-data.md) |
| Dataset interoperability (external adapter contract, LeRobot, isolated runtime, E2E) | [dataset-interoperability.md](./dataset-interoperability.md) |
| Jobs, pipelines, quality gates, execution reliability | [jobs-and-pipelines.md](./jobs-and-pipelines.md) |
| Artifact storage layout and URI conventions | [storage-layout.md](./storage-layout.md) |
| Run records and derived quality/readiness | [quality-and-runs.md](./quality-and-runs.md) |
| Reserved architecture and current limitations | [reserved-and-limitations.md](./reserved-and-limitations.md) |
| Robot data ingestion (ROS2 -> MCAP -> RobotRun) | [../workflows/robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md) |
| Local development, testing, E2E | [../development/local-development.md](../development/local-development.md) |
| Verified test coverage per capability | [../development/test-matrix.md](../development/test-matrix.md) |
| Architecture decisions | [../adr/](../adr/) |

## 6. Source-of-truth map

Where to look first when you need ground truth on a topic — code over docs
when the two disagree; file an issue/PR to fix the doc rather than trusting
the doc over the code.

**DATA** — Dataset/DatasetVersion model, config precedence
- Schema: `packages/sceneops-core/sceneops_core/datasets/schemas/`
- DB models: `packages/sceneops-db/sceneops_db/models/dataset*.py`
- API: `apps/api/app/domains/datasets/`
- Doc: [data-model.md](./data-model.md) §2

**SCENE** — Scene build/register/validate/profile/quality
- Pipeline definitions: `packages/sceneops-core/sceneops_core/pipelines/builtin.py`
  (`DATASET_SCENE_INGESTION_PIPELINE`, `RAW_LOG_SCENE_BUILDING_PIPELINE`,
  `SCENE_REGISTRATION_PIPELINE`)
- Job handlers: `apps/worker/sceneops_worker/jobs/dataset/`
- Quality: `apps/api/app/domains/scenes/quality.py`
- Doc: [scene-domain.md](./scene-domain.md)

**EPISODE** — Episode build/register/validate/profile/quality
- Pipeline definition: `RAW_LOG_EPISODE_BUILDING_PIPELINE` in
  `packages/sceneops-core/sceneops_core/pipelines/builtin.py`
- Job handlers: `apps/worker/sceneops_worker/jobs/episodes/` (episode build,
  segmentation, register, validate, profile)
- Quality: `apps/api/app/domains/episodes/quality.py`
- Doc: [episode-domain.md](./episode-domain.md)

**LEARNING DATA** — temporal alignment, validation/profiling, columnar
export, curation, native dataset/sampler/consumer adapters (Phase 2,
dispatched as standalone Jobs, no dedicated pipeline or API domain)
- Alignment/curation/native-contract: `packages/sceneops-core/sceneops_core/episodes/{alignment,curation,learning}/`
- `SceneOpsDataset`/`SequenceSampler`/adapters: `packages/sceneops-analytics/sceneops_analytics/learning_dataset/`
- Job handlers: `apps/worker/sceneops_worker/jobs/dataset/{align_episode,validate_aligned_episode,profile_aligned_episode,export_learning_data,curate_episodes}.py`
- Doc: [robot-learning-data.md](./robot-learning-data.md)

**DATASET INTEROPERABILITY** — external adapter contract, LeRobot export,
isolated runtime, real Postgres/MinIO round-trip E2E (Phase 3, complete)
- Shared adapter contract: `packages/sceneops-analytics/sceneops_analytics/external_adapters/`
- Concrete LeRobot adapter: `packages/sceneops-analytics/sceneops_analytics/external_adapters/lerobot/`
- Isolated LeRobot environment: `tools/lerobot-integration/`
- E2E: `scripts/e2e/e2e_lerobot_{resolve,export}.py`, `scripts/e2e/e2e_lerobot_roundtrip.sh` (`make e2e-lerobot`)
- Doc: [dataset-interoperability.md](./dataset-interoperability.md)

**PLATFORM** — Jobs, Pipelines, Artifacts, Executions, run records
- Job/Pipeline runners: `apps/worker/sceneops_worker/jobs/runner.py`,
  `apps/worker/sceneops_worker/pipelines/runner.py`
- Execution key/idempotency: `packages/sceneops-core/sceneops_core/executions/key.py`
- Artifact model: `packages/sceneops-db/sceneops_db/models/artifact.py`
- Docs: [jobs-and-pipelines.md](./jobs-and-pipelines.md), [quality-and-runs.md](./quality-and-runs.md)

**INFRA** — Postgres, MinIO, Redis, Celery, Docker Compose
- `compose.yaml` + `compose/*.yaml`, `Makefile` + `makefiles/*.mk`
- `packages/sceneops-db/sceneops_db/session.py` (async engine/session)
- `packages/sceneops-storage/sceneops_storage/backends/`
- Doc: [../development/local-development.md](../development/local-development.md), [storage-layout.md](./storage-layout.md)

**OPTIONAL** — Airflow backend, ROS2/robot sandbox, inference server,
scenario curation (`experimental=True`)
- Airflow: `airflow/dags/sceneops_pipeline_run.py`, [ADR-004](../adr/004-airflow-vs-celery.md)
- ROS2/robots: `ros2/`, `apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`,
  [ADR-005](../adr/005-ros2-vs-kafka-boundary.md), [robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md)
- Inference server: `apps/inference-server/`
- Scenario curation: `apps/worker/sceneops_worker/jobs/scenarios/`, [quality-and-runs.md](./quality-and-runs.md) §4
