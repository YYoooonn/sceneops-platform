# Repository structure

> How the filesystem maps to the architecture: which directories are processes, which are
> reusable production code, which are tooling, and which imports are allowed. It
> describes the current layout only. Per-subsystem behavior is in the documents linked
> from the [architecture overview](./overview.md).

## 1. Three kinds of directory

| Directory | What it is | Test |
| --- | --- | --- |
| `apps/` | An independently executable / deployable process | Could it have its own container and entrypoint? |
| `packages/` | Reusable production code imported by apps | Is it imported, not run? |
| `tools/` | Developer / CI / benchmark / fixture utilities, not part of the production runtime | Would production still work if the directory were deleted? |

```text
apps      -> packages        an app imports packages
tools     -> packages        a tool may import anything production
packages  -/-> apps          a package never imports an app
packages  -/-> tools         a package never imports a tool
apps      -/-> tools         an app never imports a tool, and no app image copies tools/
apps      -/-> apps          an app never imports another app
```

An app holds process bootstrap, framework wiring, entrypoints, runtime configuration
and the transport adapters of that executable. Domain and infrastructure logic that
another process or tool could reuse lives in a package. `make check-boundaries`
(`tools/checks/import_boundaries.py`, part of `make test`) enforces the rules above, the
package layering in §4, and that every first-party import is declared in the importing
project's `pyproject.toml`.

## 2. Applications

| App | Process | Entrypoint | Container |
| --- | --- | --- | --- |
| `apps/api` | FastAPI control plane: resources, Job / Pipeline create and dispatch | `uvicorn app.main:app` | `api` |
| `apps/worker` | Celery workers (`JobRunner`, `PipelineOrchestrator`, job handlers) and the `sceneops-worker` CLI: `jobs`, `pipelines`, `recover`, `execution-status`, `acquisition reconcile / status / artifact-lifecycle` | `celery -A sceneops_worker.celery_app.celery_app worker`, `sceneops-worker` | `worker-jobs`, `worker-pipeline` (queues), `worker-cli`, `execution-recovery`, `registration-recovery` |
| `apps/capture` | Capture: Kafka -> run lifecycle -> MCAP + capture receipt, one process per `robot_run_id` | `python -m sceneops_capture` | `capture` |
| `apps/streaming-bridge` | Integration adapter: ROS 2 topics -> Kafka (`TelemetryEnvelope`) | `python -m sceneops_streaming_bridge` | `streaming-bridge` |
| `apps/publisher` | Database-free Publisher: finalized MCAP + receipt -> ArtifactStore objects; `check`, `scan-capture`, `publish-pending` | `python -m sceneops_publisher` | `recording-publisher`, `reference-conformance`, `publication-recovery` |
| `apps/inference-server` | GroundingDINO HTTP server called by `predict_detection` | `uvicorn inference_server.main:app` | `inference-server-local`, `inference-server-gpu` |

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
  `WorkerContext` (see §6 for why these are not packages).
- `apps/capture`, `apps/streaming-bridge`, `apps/publisher`: argument parsing, settings,
  signal handling and the call into the package that implements the capability. The two
  ROS 2 apps run in `ros:jazzy` images because they need `rclpy` (bridge) or the ROS 2
  interface definitions (capture); nothing else in SceneOps depends on ROS 2.

## 3. Packages

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

## 4. Package layering

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

## 5. Acquisition boundary

```text
external robot / simulator / replay      (produces robot experience; outside SceneOps)
  -> ROS 2 (or an equivalent source)
  -> apps/streaming-bridge               integration adapter: topics -> TelemetryEnvelope
  -> Kafka                               transport only; bounded replay, never canonical
  -> apps/capture                        Kafka consumer -> run lifecycle -> MCAP + receipt
       (sceneops_recording.capture)
  -> apps/publisher                      MCAP + receipt -> published RobotRun objects
       (sceneops_recording publication)
  -> REGISTER_ROBOT_RUN                  -> RobotRun (the canonical ingestion boundary)
```

The producer is not part of SceneOps: `tools/dataset-acquisition` is a tool that plays
the producer for reference datasets (it converts an external dataset to an MCAP and
replays it over ROS 2) and depends on no SceneOps package. Capture finalizes a
lifecycle-complete stream only after the run's `RUN_END`; there is no multi-run router.
Details: [Streaming transport](./streaming-transport.md) and
[Robot data ingestion](../workflows/robot-run-and-mcap.md).

## 6. Deliberate boundaries

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

## 7. Tools

| Directory | Contents |
| --- | --- |
| `tools/checks/` | Static and container checks: command surface, import boundaries, runtime source boundary, acquisition image isolation, environment and broker diagnostics |
| `tools/e2e/` | The four E2E journeys, their shared shell library and container-run verifiers |
| `tools/baselines/` | Recording Import (`canonical/`) and Streaming Acquisition (`streaming/`) baseline bootstrap, verify and compare |
| `tools/reference/` | Reference corpus preparation and the golden reference contract's bootstrap / verifier |
| `tools/benchmarks/` | Measurement tooling; no command runs it and none is acceptance |
| `tools/dev/` | Local environment helpers: setup, reset, MinIO bucket init, disk report, polling loop |
| `tools/dataset-acquisition/` | Own uv project: external dataset -> MCAP, and MCAP -> ROS 2 replay |
| `tools/lerobot-integration/` | Own uv project: the LeRobot export runtime |

## 8. Where tests live

- Package unit tests: `packages/<name>/tests/`. A test of an app's own wiring:
  `apps/<name>/tests/`.
- Tests that span apps and need ROS 2 containers: `tests/streaming/`
  (`make streaming-test`). Tests that need real PostgreSQL, MinIO, Redis or Celery
  workers: `tests/infrastructure/` and the `*_integration.py` modules
  (`make test-integration`, `make test-infrastructure`).
- Test builders that several suites share are production-package modules named
  `testing` (for example `sceneops_recording.testing`); production code never imports
  them.
- `tools/*/tests/` test the tool itself.

Run commands: [test matrix](../development/test-matrix.md).
