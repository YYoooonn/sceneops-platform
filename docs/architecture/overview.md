# SceneOps Architecture

> The authoritative map of the platform as implemented. It describes the current
> system only; decision rationale lives in [`../adr/`](../adr/), point-in-time
> studies in [`../history/`](../history/), and what the platform does not do in
> [Current limitations](./limitations.md).

## 1. System overview

SceneOps is a control plane, an execution plane, an acquisition path and a storage
layer, connected through shared libraries under `packages/`:

```text
Control plane   apps/api              FastAPI: resources, Job / Pipeline create + dispatch
      |
      v   Redis (Celery broker)
Execution       apps/worker           Celery workers: PipelineOrchestrator, JobRunner, handlers
Acquisition     apps/capture          Kafka -> MCAP + capture receipt, one process per run
                apps/publisher        MCAP + receipt -> published RobotRun objects (database-free)
      |
      v
Storage         PostgreSQL            identity, metadata, execution state, lineage references
                ArtifactStore         immutable bytes: recordings, manifests, reports, Parquet

External        integrations/ros2-kafka-bridge     ROS 2 topics -> Kafka (feeds Capture)
(adapters)      integrations/groundingdino-server  GroundingDINO HTTP model server (called by predict_detection)
                integrations/dataset-acquisition   external dataset -> MCAP; MCAP -> ROS 2 replay
                integrations/lerobot               LeRobot export runtime
```

`apps/` holds the processes SceneOps owns (`api`, `worker`, `capture`, `publisher`),
`packages/` the reusable production code they import (`sceneops-core`, `-db`,
`-storage`, `-streaming`, `-recording`, `-execution`, `-acquisition`, `-scenes`,
`-episodes`, `-derived`, `-inference`, `-evaluation`, `-analytics`), `integrations/`
the adapters between SceneOps and external protocols, runtimes and data sources, and
`tools/` the developer, CI and benchmark utilities. Apps and integrations import
packages; packages never import apps, integrations or tools. An integration may run as
a container without being a SceneOps app. The ownership of each directory and the
dependency rules are in [Repository structure](./repository-structure.md).

`sceneops-core` never touches a database or object store: it defines the
`ArtifactStore` Protocol and the Pydantic schemas, and `sceneops-db` /
`sceneops-storage` supply the implementations that the API and worker inject.
This port/adapter split is why the storage backend (local filesystem or
MinIO / S3) changes without call-site changes ([ADR-002](../adr/002-object-storage-for-assets.md)).

## 2. Domain structure

```text
Acquisition -> RobotRun (ingestion boundary)
RobotRun    -> Scene     (canonical, source-faithful)
RobotRun    -> Episode   (canonical, task-oriented)
Scene       -> labels / sample views / ScenarioSets / predictions / evaluations
Episode     -> AlignedEpisode -> LearningDataExport
```

- **RobotRun** is the single ingestion boundary: one finalized, published,
  verified recording, created only by `REGISTER_ROBOT_RUN`. Nothing downstream of it
  knows whether the recording arrived by batch import or by streaming acquisition.
- **Scene** and **Episode** are sibling derived domains over the same RobotRun, each
  with its own build, registration, validation and quality contract. They share
  platform infrastructure (Jobs, Pipelines, ArtifactStore), not a model: there is no
  generic "data unit" type. See [Scene domain](./scene-domain.md) and
  [Episode domain](./episode-domain.md).
- Derived L3 objects are immutable, checksum-pinned revisions that record the
  revisions they consumed. See [Derived layer](./derived-layer.md).

## 3. Acquisition

Two acquisition paths end at the same RobotRun boundary:

```text
Batch / reference
  MCAP -> Publisher -> published RobotRun objects -> registration / reconciliation -> RobotRun

Streaming
  EXTERNAL   robot / simulator / dataset / replay -> source adapter
               -> integrations/ros2-kafka-bridge -> Kafka
  SCENEOPS   Kafka -> apps/capture -> finalized MCAP + capture receipt
               -> apps/publisher -> registration / reconciliation -> RobotRun
```

SceneOps begins at the Kafka topic. The producer of robot experience and the ROS 2
bridge that adapts it onto the transport are outside SceneOps (ROS 2 is one integration,
not a SceneOps requirement). Kafka is bounded replay, never canonical storage. Capture (`apps/capture`, implemented in
`sceneops-recording`) is one one-shot process per `robot_run_id`;
a capture that expects a lifecycle-complete run finalizes only after the run's
`RUN_END`. The handoff to the Publisher is durable state, not a notification: a
finalized MCAP with its capture receipt is what the Publisher discovers and publishes,
so Capture need not be alive. Publication and registration are recoverable one-shot
commands, not triggered by capture. See [Streaming transport](./streaming-transport.md) and
[Robot data ingestion](../workflows/robot-run-and-mcap.md).

## 4. Control plane — `apps/api`

```text
apps/api/app/
  domains/            resource-centric domain APIs
    datasets/ scenes/ episodes/ scenarios/ robots/ inference/ evaluations/ models/
  platform/           execution infrastructure API (routers and dependencies; the services
                      are in sceneops-execution)
    jobs/             Job create / query / dispatch routes
    pipelines/        PipelineRun create / query / dispatch routes, built-in definitions
    executions/       ExecutionRecord routes
    artifacts/        artifact metadata query
  views/              aggregate / cross-domain APIs (leaderboards, operations)
```

Each domain is layered `router.py` -> `service.py` -> `sceneops-db` repository,
wired by FastAPI dependencies. The Job, Pipeline and Execution services, their dispatch
facades and the Celery dispatch backends are `sceneops-execution`; the
`REGISTER_ROBOT_RUN` submission service is `sceneops-acquisition`.

### Transaction model

A mutating API request is one PostgreSQL transaction, and a success response means
that transaction has committed. `get_db_session` (`apps/api/app/core/dependencies.py`)
is a function-scoped dependency: the session commits when the path operation
returns, before the response is sent, and rolls back on any exception
(`HTTPException` included). A failed commit is an error response, so a client may
act on a 2xx at once. Services and repositories flush but never commit.

Operations that must reach an external system follow a durable-state-first
boundary: persist and commit, then dispatch. The dispatch facades and
`RobotRunRegistrationService` open short sessions of their own and commit
explicitly before sending to Celery, so no transaction is open while talking to the
broker and a worker never receives a reference to a row that is not yet durable.
Workers do not auto-commit: `JobRunner` and `PipelineOrchestrator` commit at their
own checkpoints. [Jobs and pipelines](./jobs-and-pipelines.md) §5 gives the failure
windows this leaves.

### Dispatch

The API never executes work. `POST /jobs` and `POST /pipelines/runs` create a
durable record; `POST /jobs/{id}/execute` and `POST /pipelines/runs/{id}/execute`
go through `JobDispatchFacade` / `PipelineDispatchFacade`, which (1) mark the record
`QUEUED` and commit, (2) send one Celery message through `ExecutionService`, and (3)
commit the resulting `ExecutionRecord`. Committing first prevents a delayed `QUEUED`
commit from overwriting a state a fast worker already advanced. If the send fails,
the record stays `QUEUED` and can be dispatched again.

## 5. Execution — `apps/worker`

Celery is the only execution backend. Exactly two tasks exist
(`sceneops_worker/tasks/`), on two queues:

```text
sceneops.jobs           run_job_task(job_id)                   -> JobRunner.run(job_id)
sceneops.pipeline_runs  advance_pipeline_task(pipeline_run_id) -> PipelineOrchestrator.advance(id)
```

The canonical execution path is:

```text
PipelineRun -> PipelineTaskRun -> durable Job -> asynchronous dispatch
            -> Celery worker -> JobRunner -> domain handler
```

- **JobRunner** (`sceneops_worker/jobs/runner.py`) is the sole runtime entry point
  for a Job: it claims the Job, runs the registered handler, and persists the
  terminal state. A Job that belongs to a PipelineRun is then reported to the
  pipeline queue.
- **PipelineOrchestrator** (`sceneops_worker/pipelines/orchestrator.py`) never runs
  a handler or a JobRunner. Each `advance` is one short, repeatable step over durable
  state: it settles the finished task's Job, applies the quality gate, or creates,
  commits and dispatches the next task's Job, then returns.
- Handlers are registered per `JobType` in `JobHandlerRegistry`.
- The mechanics that do not depend on the worker's context are in `sceneops-execution`:
  ownership leases (`jobs/lease.py`), lease recovery, execution recovery, job event and
  result recording, the pipeline quality gate and result builder, and the Celery
  dispatcher. The domain logic the handlers call is in `sceneops-scenes`,
  `sceneops-episodes`, `sceneops-inference`, `sceneops-evaluation` and
  `sceneops-derived`.

Details, retry semantics and failure windows: [Jobs and pipelines](./jobs-and-pipelines.md).

The inference capability is core (`sceneops-inference`: backends, the GroundingDINO
HTTP client, prediction publication). The model server (`integrations/groundingdino-server`)
is an external runtime: a separate FastAPI process that runs GroundingDINO, which the
worker's `predict_detection` handler calls over HTTP, keeping torch / transformers out
of the worker image.

## 6. Storage

```text
PostgreSQL                          ArtifactStore (object storage)
  canonical identity                  raw recordings, RobotRunManifests
  metadata, membership                Scene / Episode manifests, payloads
  transactional + execution state     label sets, sample views, ScenarioSets
  lineage references (ArtifactRecord) predictions, evaluations, reports
                                      Parquet analytical tables, learning exports

Redis    Celery broker / result backend only
Kafka    streaming transport only
```

- PostgreSQL is authoritative for durable identity, metadata, execution state and
  lineage references. It stores no payloads, only ArtifactStore URIs and checksums.
- The ArtifactStore is authoritative for immutable recording and artifact bytes and
  for manifests. Derived artifacts are write-once at checksum-qualified keys and an
  `ArtifactRecord` names those exact bytes ([Storage layout](./storage-layout.md)).
- Manifests, indexes, profile summaries and exports are derived and reproducible
  from canonical state plus durable source artifacts; none of them is identity.
- `create_artifact_store(settings)` selects `LocalArtifactStore` or
  `S3ArtifactStore` (MinIO included) from the `ArtifactBackend` setting (`local`,
  `minio`, `s3`).

## 7. Not part of the runtime

SceneOps has no other execution backend and no alternative orchestrator: pipelines
are not run inline in a worker process, and no external workflow engine (Airflow)
dispatches them. A Pipeline is durable state advanced by the orchestrator, and every
unit of domain work is a Job. There is no multi-run capture router; capture is one
process per run. The rationale and the superseded decisions are in
[ADR-009](../adr/009-job-centric-execution-and-durable-boundaries.md),
[ADR-004](../adr/004-airflow-vs-celery.md) (superseded) and
[ADR-008](../adr/008-acquisition-lifecycle-reliability.md).

## 8. Documentation map

| Tier | Location | Answers |
| --- | --- | --- |
| Orientation | [`README.md`](../../README.md) | What is SceneOps, how do I start |
| Architecture | `docs/architecture/` | How the system works now |
| Workflows | `docs/workflows/` | End-to-end data flows as implemented |
| Development | `docs/development/` | Commands, test surface, reference environment |
| Decisions | `docs/adr/` | Why a decision was made |
| History | `docs/history/` | What was measured or studied at a point in time (not current) |
| Roadmap | none maintained | Future work lives in issues and PRs, not in this tree |

| Topic | Document |
| --- | --- |
| Entities and relationships | [data-model.md](./data-model.md) |
| Scene domain | [scene-domain.md](./scene-domain.md) |
| Episode domain | [episode-domain.md](./episode-domain.md) |
| Labels, sample views, ScenarioSets, predictions, evaluation, aligned episodes | [derived-layer.md](./derived-layer.md) |
| Robot learning data layer | [robot-learning-data.md](./robot-learning-data.md) |
| Scalable learning data (production layout, frozen contracts) | [scalable-learning-data.md](./scalable-learning-data.md) |
| Dataset interoperability (adapter contract, LeRobot) | [dataset-interoperability.md](./dataset-interoperability.md) |
| External integration runtime | [external-integration-runtime.md](./external-integration-runtime.md) |
| Repository structure: apps, packages, integrations, tools, dependency rules | [repository-structure.md](./repository-structure.md) |
| Streaming transport, bridge, capture, run lifecycle | [streaming-transport.md](./streaming-transport.md) |
| Jobs, pipelines, quality gates, execution reliability | [jobs-and-pipelines.md](./jobs-and-pipelines.md) |
| Artifact layout, URI conventions, publication | [storage-layout.md](./storage-layout.md) |
| Run records, quality and readiness | [quality-and-runs.md](./quality-and-runs.md) |
| Current limitations | [limitations.md](./limitations.md) |
| Robot data ingestion (MCAP -> RobotRun) | [../workflows/robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md) |
| Local development, test matrix | [../development/local-development.md](../development/local-development.md), [../development/test-matrix.md](../development/test-matrix.md) |

## 9. Source-of-truth map

Where to look first when you need ground truth; code and tests win over any
document.

**DATA** — Dataset / DatasetVersion
- Schema `packages/sceneops-core/sceneops_core/datasets/schemas/`; DB models
  `packages/sceneops-db/sceneops_db/models/dataset*.py`; API `apps/api/app/domains/datasets/`
- Doc: [data-model.md](./data-model.md)

**SCENE** — canonical SceneManifest, registration, validate / profile / quality
- Schema `packages/sceneops-core/sceneops_core/scenes/`; builder / registration
  builder / validator / profiler `packages/sceneops-scenes/sceneops_scenes/`, registration
  `apps/worker/sceneops_worker/scenes/`; recording reader
  `packages/sceneops-recording/sceneops_recording/reader.py`
- Pipeline definition `RECORDING_SCENE_BUILDING_PIPELINE` in
  `packages/sceneops-core/sceneops_core/pipelines/builtin.py`
- Handlers `apps/worker/sceneops_worker/jobs/dataset/`; quality `apps/api/app/domains/scenes/quality.py`
- Doc: [scene-domain.md](./scene-domain.md)

**EPISODE** — build / register / validate / profile / quality
- Pipeline definition `RECORDING_EPISODE_BUILDING_PIPELINE` (same file); builder /
  `packages/sceneops-episodes/sceneops_episodes/`, registration
  `apps/worker/sceneops_worker/episodes/`; shared payload extraction
  `packages/sceneops-recording/sceneops_recording/observations/`; quality
  `apps/api/app/domains/episodes/quality.py`
- Doc: [episode-domain.md](./episode-domain.md)

**LEARNING DATA** — alignment, validation / profiling, columnar export, curation, native dataset
- `packages/sceneops-core/sceneops_core/episodes/{alignment,curation,learning}/`,
  `packages/sceneops-analytics/sceneops_analytics/learning_dataset/`,
  handlers `apps/worker/sceneops_worker/jobs/dataset/{align_episode,validate_aligned_episode,profile_aligned_episode,export_learning_data,curate_episodes}.py`
- Docs: [robot-learning-data.md](./robot-learning-data.md), [scalable-learning-data.md](./scalable-learning-data.md)

**DATASET INTEROPERABILITY / EXTERNAL RUNTIME**
- `packages/sceneops-analytics/sceneops_analytics/external_adapters/` (shared contract,
  `lerobot/` adapter); `packages/sceneops-core/sceneops_core/integration_runtime/`;
  isolated environment `integrations/lerobot/`
- Docs: [dataset-interoperability.md](./dataset-interoperability.md),
  [external-integration-runtime.md](./external-integration-runtime.md)

**EXECUTION** — Jobs, Pipelines, dispatch, execution records
- Services and dispatch `packages/sceneops-execution/sceneops_execution/`; API routes
  `apps/api/app/platform/{jobs,pipelines,executions}/`
- Worker: `apps/worker/sceneops_worker/jobs/runner.py`,
  `apps/worker/sceneops_worker/pipelines/orchestrator.py`,
  `apps/worker/sceneops_worker/tasks/`
- Task / queue names `packages/sceneops-core/sceneops_core/constants/tasks.py`;
  execution key `packages/sceneops-core/sceneops_core/executions/key.py`
- Doc: [jobs-and-pipelines.md](./jobs-and-pipelines.md)

**ARTIFACTS** — publication, records, layout
- `apps/worker/sceneops_worker/derived/publication.py`,
  `packages/sceneops-db/sceneops_db/models/artifacts.py`,
  `packages/sceneops-storage/sceneops_storage/backends/`
- Doc: [storage-layout.md](./storage-layout.md)

**ACQUISITION / STREAMING**
- ROS 2 -> Kafka integration `integrations/ros2-kafka-bridge/`, capture `apps/capture/` +
  `packages/sceneops-recording/sceneops_recording/capture/`, publisher `apps/publisher/` +
  `packages/sceneops-recording/sceneops_recording/`, transport `packages/sceneops-streaming/`,
  lifecycle commands `packages/sceneops-acquisition/` (run as `sceneops-worker acquisition ...`)
- Docs: [streaming-transport.md](./streaming-transport.md),
  [robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md),
  [ADR-005](../adr/005-ros2-vs-kafka-boundary.md), [ADR-008](../adr/008-acquisition-lifecycle-reliability.md)

**INFRA** — Postgres, MinIO, Redis, Celery, Docker Compose
- `compose.yaml` + `compose/*.yaml`, `Makefile` + `makefiles/*.mk`,
  `packages/sceneops-db/sceneops_db/session.py`, `packages/sceneops-storage/sceneops_storage/backends/`
- Docs: [local-development.md](../development/local-development.md), [storage-layout.md](./storage-layout.md)
