# Repository structure

> How the filesystem maps to the architecture: which directories are SceneOps processes,
> reusable production code, external integrations or tooling, and which imports are allowed. It
> describes the current layout only. Per-subsystem behavior is in the documents linked
> from the [architecture overview](./overview.md).

## 1. Four kinds of directory

| Directory | Meaning | Classification question |
| --- | --- | --- |
| `apps/` | Canonical SceneOps runtime processes | Does SceneOps itself own and operate this process? |
| `packages/` | Reusable production code, named by capability | Is this production capability imported and reused, rather than the executable boundary itself? |
| `integrations/` | Adapters between SceneOps and an external protocol, runtime, ecosystem or data source | Does this executable exist mainly to connect SceneOps with something outside SceneOps? |
| `tools/` | Engineering and operator tooling | Is this used to develop, validate, benchmark, test, inspect or operate the repository rather than provide a product or runtime integration? |

```text
apps          -> packages        an app imports packages
integrations  -> packages        an integration may import the SceneOps packages that
                                 implement the contract it adapts (possibly none)
tools         -> packages        a tool may import production code
packages      -/-> apps          a package never imports an app
packages      -/-> integrations  a package never imports an integration
packages      -/-> tools         a package never imports a tool
apps          -/-> integrations  an app never imports an integration
apps          -/-> tools         an app never imports a tool, and no image copies tools/
apps          -/-> apps          an app never imports another app
integrations  -/-> apps, integrations, tools
```

An app holds process bootstrap, framework wiring, entrypoints and runtime configuration.
Domain and infrastructure logic that another process, integration or tool could reuse
lives in a package. An integration may have its own Python / uv project, dependency set,
Docker image, entrypoint and tests; SceneOps reaches it over its external protocol (a
Kafka topic, an HTTP endpoint, a file) or runs it as a separate process, never by
importing it. `make check-boundaries` (`tools/checks/import_boundaries.py`, part of
`make test`) enforces the rules above on production code, the package layering in §5,
that every first-party import is declared in the importing project's `pyproject.toml`,
that no app, package or integration declares a dependency on an app or an integration,
that no image copies another category's source, and that every Compose build context and
Dockerfile exists.

**Classification is ownership, not deployment.** Something under `integrations/` may
still have a Dockerfile, run as a Docker Compose service, define readiness checks and
take part in E2E tests (the GroundingDINO server runs as a container during real
inference runs; the ROS 2 Kafka bridge runs in the `streaming` Compose profile). That
does not make it a SceneOps app. The directory says who owns the boundary; Compose says
how it is run.

## 2. Applications

| App | Process | Entrypoint | Container |
| --- | --- | --- | --- |
| `apps/api` | FastAPI control plane: resources, Job / Pipeline create and dispatch | `uvicorn app.main:app` | `api` |
| `apps/worker` | Celery workers (`JobRunner`, `PipelineOrchestrator`, job handlers) and the `sceneops-worker` CLI: `jobs`, `pipelines`, `recover`, `execution-status`, `acquisition reconcile / status / artifact-lifecycle` | `celery -A sceneops_worker.celery_app.celery_app worker`, `sceneops-worker` | `worker-jobs`, `worker-pipeline` (queues), `worker-cli`, `execution-recovery`, `registration-recovery` |
| `apps/capture` | Capture: Kafka -> run lifecycle -> MCAP + capture receipt, one process per `robot_run_id` | `python -m sceneops_capture` | `capture` |
| `apps/publisher` | Database-free Publisher: finalized MCAP + receipt -> ArtifactStore objects; `check`, `scan-capture`, `publish-pending` | `python -m sceneops_publisher` | `recording-publisher`, `reference-conformance`, `publication-recovery` |

`apps/worker` is one codebase deployed as several containers that differ only in
command and queue. Execution recovery (`sceneops-worker recover`) is a worker command
looped by `execution-recovery`; it is not a separate app, because it needs exactly the
worker's database, broker and Celery app.

What each app keeps for itself:

- `apps/api`: routers, request / response schemas, FastAPI dependencies, HTTP error
  mapping, API settings, application startup.
- `apps/worker`: the Celery app and its two tasks, process lifecycle hooks, CLI wiring,
  `WorkerContext` and its dependency construction, `JobRunner`, `PipelineOrchestrator`,
  the job handlers and the registration / resolution use cases that run on a
  `WorkerContext` (see §7 for why these are not packages).
- `apps/capture`, `apps/publisher`: argument parsing, settings, signal handling and the
  call into the package that implements the capability. Capture runs in a `ros:jazzy`
  image because it needs the ROS 2 interface definitions to decode recorded messages;
  nothing else in SceneOps depends on ROS 2.

Capture and Publisher are applications, not integrations: Capture turns an ephemeral
robot stream into a complete durable recording and decides whether the recording is
complete; the Publisher turns a complete recording into an immutable SceneOps data
asset. Both are owned and operated by the platform.

## 3. Integrations

| Integration | Connects SceneOps to | Project | Entrypoint | Container |
| --- | --- | --- | --- | --- |
| `integrations/ros2-kafka-bridge` | ROS 2 (robot, simulator, replay): ROS 2 topics -> `TelemetryEnvelope` -> Kafka | workspace member `sceneops-streaming-bridge`; imports `sceneops-core`, `sceneops-streaming` | `python -m sceneops_streaming_bridge` | `streaming-bridge` |
| `integrations/groundingdino-server` | The GroundingDINO model runtime: HTTP detection endpoint | workspace member `sceneops-inference-server`; imports no SceneOps package | `uvicorn inference_server.main:app` | `inference-server-local`, `inference-server-gpu` |
| `integrations/dataset-acquisition` | External datasets (nuScenes) and ROS 2 replay: dataset -> acquisition MCAP, MCAP -> ROS 2 topics | own uv project and `uv.lock`; imports no SceneOps package | `dataset-acquisition` | `dataset-acquisition`, `dataset-replay`, `reference-data`, `reference-labels` |
| `integrations/lerobot` | The LeRobot ecosystem: the isolated LeRobot export runtime | own uv project and `uv.lock` (lerobot needs numpy>=2); runs `sceneops_analytics`' adapter from `sceneops-core`, `-storage`, `-analytics` | `sceneops_analytics.external_adapters.lerobot.entrypoint` | `lerobot-integration` |

Each integration is independently understandable and keeps its own dependency set; there
is no shared integration package. An integration communicates with SceneOps through a
protocol or a durable file, or imports a SceneOps package to implement a SceneOps
contract; SceneOps production code never imports the integration. In particular:

- The bridge and Capture share one transport contract through `sceneops-streaming`
  (`TelemetryEnvelope`, channel definitions, lifecycle control events, Kafka adapters),
  which stays a production package. The bridge depends on it; it does not depend on the
  bridge.
- `sceneops-inference` owns the inference capability: backend abstractions, the mock
  backend, the GroundingDINO HTTP client, frustum lifting and prediction publication.
  The GroundingDINO server is one concrete external model-serving runtime that the client
  calls over HTTP; its implementation is outside the package dependency graph.
- `integrations/dataset-acquisition` produces the MCAP that enters SceneOps through the
  Publisher and `REGISTER_ROBOT_RUN`; it imports no SceneOps package, and its
  import-boundary test and image check keep it that way.
- `integrations/lerobot` contains no domain or data logic of its own; the adapter it
  executes lives in `sceneops-analytics`.

## 4. Packages

| Package | Owns |
| --- | --- |
| `sceneops-core` | Domain schemas, IDs, enums, Protocol contracts, pipeline and job definitions, configuration models. No I/O: no database, broker, object store or HTTP client |
| `sceneops-db` | SQLAlchemy models, repositories, PostgreSQL implementations (including the atomic conditional updates whose correctness is PostgreSQL semantics), sessions; Alembic migrations live in `migrations/` |
| `sceneops-storage` | `ArtifactStore` implementations (local, S3 / MinIO) and the write-once primitive |
| `sceneops-streaming` | Streaming transport: `TelemetryEnvelope`, channel registry, run lifecycle control events, Kafka producer / consumer, wire mapping. No RobotRun, Scene or Episode semantics |
| `sceneops-recording` | Recording: `capture/` (run-scoped consumer, MCAP writer, validation, atomic finalize, capture receipt I/O), publication of finalized recordings, L1 conformance and equivalence, the recording reader, the capture receipt / scan / published-scan vocabulary, canonical observation payload extraction (`observations/`), test builders (`testing/`) |
| `sceneops-execution` | Job and Pipeline services and dispatch facades, Celery dispatch backends and the worker-side dispatcher, ownership leases and lease recovery, execution recovery, job event and result recording, pipeline quality gate and result builder |
| `sceneops-acquisition` | Acquisition lifecycle (ADR-008): reconciliation and bounded registration recovery, derived acquisition status, artifact lifecycle classification, `REGISTER_ROBOT_RUN` submission |
| `sceneops-scenes` | Scene building from a registered recording, Scene validation and profiling, Scene artifact layout |
| `sceneops-episodes` | Episode building from a registered recording, Episode validation and profiling, Episode artifact layout |
| `sceneops-derived` | Write-once, checksum-pinned storage of derived manifests (labels, sample views, ScenarioSets, predictions) and run artifacts |
| `sceneops-inference` | Detection inference backends (mock, GroundingDINO client with frustum lifting) and prediction publication |
| `sceneops-evaluation` | Detection evaluation: matching, metric accumulation, evaluation artifacts |
| `sceneops-analytics` | Parquet table builders, DuckDB helper, learning-data read layer, external dataset adapters |

Scene and Episode are sibling packages: neither imports the other, and no generic "data
unit" package exists. Their shared infrastructure is `sceneops-recording` (payload
extraction) and `sceneops-derived`.

## 5. Package layering

```text
0  sceneops-core
1  sceneops-storage    sceneops-db    sceneops-streaming
2  sceneops-recording (storage; streaming from capture/ only)
   sceneops-derived   (storage)
   sceneops-analytics (storage)
   sceneops-execution (db)
3  sceneops-scenes, sceneops-episodes        (recording)
   sceneops-inference, sceneops-evaluation   (derived)
   sceneops-acquisition                      (db, storage, recording, execution)
```

Every package depends on `sceneops-core`; the parenthesis names its other dependencies.

The allowed edges are the table in `tools/checks/import_boundaries.py`. Notes:

- `sceneops-streaming` is reachable from `sceneops-recording` only through its `capture`
  subpackage (the `capture` extra); publication, reading and conformance do not import
  it, so the publisher image carries no Kafka client.
- `sceneops-recording` and `sceneops-streaming` never import `sceneops-db`; Capture and
  the Publisher are database-free by construction.
- Packages that need PostgreSQL (`sceneops-execution`, `sceneops-acquisition`) sit above
  `sceneops-db`; nothing in `sceneops-db` imports an application-level package.

## 6. Acquisition and inference boundaries

### Acquisition

```text
EXTERNAL (outside SceneOps)

robot / simulator / dataset / replay
  -> source adapter
  -> integrations/ros2-kafka-bridge     ROS 2 topics -> TelemetryEnvelope -> Kafka producer
  -> Kafka                              transport and bounded replay; never canonical storage

======================= SceneOps streaming boundary =======================

  -> apps/capture                       Kafka consumer -> run lifecycle -> MCAP + capture receipt
       (sceneops_recording.capture)      finalized only after RUN_END
  -> finalized MCAP + capture receipt   durable state, not a notification
  -> apps/publisher                     scan / validate -> immutable content-addressed objects
       (sceneops_recording publication)
  -> durable published recording
  -> REGISTER_ROBOT_RUN
  -> RobotRun                           the canonical platform ingestion identity
```

- Kafka is transport, not canonical data storage. Capture is streaming ingestion; the
  Publisher is durable publication; the RobotRun is the canonical identity.
- ROS 2 is one integration, not a SceneOps requirement: any producer that emits
  `TelemetryEnvelope` records and the run lifecycle events onto the transport can feed
  Capture. SceneOps begins at the Kafka topic.
- `integrations/dataset-acquisition` plays the producer for reference datasets (it
  converts an external dataset to an MCAP and replays it over ROS 2) and depends on no
  SceneOps package.

**Capture -> Publisher handoff.** Capture does not call the Publisher, and the Publisher
needs no completion message from Capture. The handoff is durable state:

```text
Capture:    .partial/<robot_run_id>/ ...        a recording in progress or interrupted
            RUN_END observed, MCAP validated
            -> atomic rename to <robot_run_id>/ with capture_receipt.json
                                                  a finalized recording
Publisher:  scan-capture / publish-pending       classifies runs on the capture root,
            publishes every finalized run with a valid receipt that is not completely
            published
```

A recording that never saw `RUN_END` stays partial and is never published. A finalized
recording is rediscovered by scanning the capture root, so Capture and the Publisher fail
and restart independently, a Publisher that was down during finalization catches up
later, and nothing depends on Capture still being alive. Capture finalizes a
lifecycle-complete stream only after the run's `RUN_END`; one Capture process is scoped
to one RobotRun, and there is no multi-run router. Details:
[Streaming transport](./streaming-transport.md) and
[Robot data ingestion](../workflows/robot-run-and-mcap.md).

### Inference

```text
SceneOps

Inference Job
  -> sceneops-inference               backend selection, parameters, prediction publication
  -> InferenceBackend / HTTP client
       | HTTP

======================= external runtime boundary =======================

  -> integrations/groundingdino-server    model process, model loading, CUDA / runtime,
                                          the detection endpoint
```

SceneOps owns selecting the inference data, executing the inference Job, model and
model-version metadata, inference parameters, prediction publication, lineage and
evaluation. The inference capability is core and therefore production code in
`sceneops-inference`; the GroundingDINO server is one concrete external model-serving
runtime, owned by the integration.

## 7. Deliberate boundaries

- **The worker keeps `WorkerContext`-bound code.** `JobRunner`, `PipelineOrchestrator`,
  the job handlers and the registration / resolution use cases take a `WorkerContext`
  (the aggregate of the database stores and artifact stores of one job execution).
  Moving them into packages would need a context abstraction that does not exist, so
  they remain in `apps/worker`. Everything that does not depend on the context (lease
  and recovery mechanics, builders, validators, profilers, artifact layouts, inference
  and evaluation logic) is in a package.
- **`sceneops-core` is the domain contract layer, not only identifiers.** It holds the
  Pydantic schemas of every domain, which `sceneops-db`, the API, the worker and the
  packages above all share, plus some pure domain logic (episode alignment, curation and
  learning projections). `sceneops-db`'s repositories and converters
  are written in terms of these schemas and the Job parameter schemas reference them, so
  moving them into the capability packages above `sceneops-db` would reverse that
  dependency.
- **The API's Job and Pipeline services live in `sceneops-execution`.** They return the
  API's response models, which therefore live there too (`jobs/schemas.py`,
  `pipelines/schemas.py`, `executions/schemas.py`), so that the registration and
  reconciliation commands can use the same submission path as the HTTP routes.

## 8. Tools

| Directory | Contents |
| --- | --- |
| `tools/checks/` | Static and container checks: command surface, import boundaries, runtime source boundary, acquisition image isolation, environment and broker diagnostics |
| `tools/e2e/` | The four E2E journeys, their shared shell library and container-run verifiers |
| `tools/baselines/` | Recording Import (`canonical/`) and Streaming Acquisition (`streaming/`) baseline bootstrap, verify and compare |
| `tools/reference/` | Reference corpus preparation and the golden reference contract's bootstrap / verifier |
| `tools/benchmarks/` | Measurement tooling; no command runs it and none is acceptance |
| `tools/dev/` | Local environment helpers: setup, reset, MinIO bucket init, disk report, polling loop |

## 9. Where tests live

- Package unit tests: `packages/<name>/tests/`. A test of an app's own wiring:
  `apps/<name>/tests/`. Tests of one integration: `integrations/<name>/tests/` (the
  bridge's run in its ROS 2 image, the GroundingDINO server's mock the model, the
  dataset-acquisition tests run in its own uv project).
- Cross-system tests stay outside any one project. Tests that span the bridge and Capture
  and need ROS 2 containers: `tests/streaming/` (`make streaming-test`). Tests that need
  real PostgreSQL, MinIO, Redis or Celery workers: `tests/infrastructure/` and the
  `*_integration.py` modules (`make test-integration`, `make test-infrastructure`).
- Test builders that several suites share are production-package modules named
  `testing` (for example `sceneops_recording.testing`); production code never imports
  them.
- `tools/*/tests/` test the tool itself.

Run commands: [test matrix](../development/test-matrix.md).
