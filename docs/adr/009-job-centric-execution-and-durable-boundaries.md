# ADR-009: Job-centric execution and durable boundaries

## Status

Accepted — implemented. Supersedes [ADR-004](./004-airflow-vs-celery.md).

This ADR records the execution, transaction and artifact-publication decisions of the
current platform. The behavior it describes is documented in
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) and
[Storage layout](../architecture/storage-layout.md); this record explains why the
design has this shape. Where it names removed components, they are history: Git holds
their code.

## Context

[ADR-004](./004-airflow-vs-celery.md) split execution between Celery (Jobs, and
Pipelines by default) and an Airflow proof of concept (Pipelines, as per-task DAGs).
A Pipeline could therefore run in two ways: inline in one Celery process through a
`PipelineRunner`, or as an Airflow DAG whose tasks invoked a task runner directly. In
both, the pipeline's own process created each task's Job and then executed it inline
through `JobRunner`. Jobs were durable, but running one was not decoupled from running
the pipeline: the pipeline process was an execution path for Jobs in its own right, held
its worker for the whole run, and every guarantee had to stay true on two orchestration
paths.

Two further boundaries were implicit:

- an API request's transaction committed after the response was built, so a success
  response did not yet guarantee committed state;
- derived artifacts were not uniformly write-once: a key could be rewritten under a
  record that had pinned it, and a retried producer could register duplicate records
  for the same content.

## Decision

### 1. Jobs are the sole executable unit; Pipelines are durable orchestration state

```text
PipelineRun -> PipelineTaskRun -> durable Job -> asynchronous dispatch
            -> Celery worker -> JobRunner -> domain handler
```

- `JobRunner` is the only runtime entry point that executes a Job. A pipeline-owned Job
  and a standalone Job run through the same code, so they share one claim, result and
  failure path.
- `PipelineOrchestrator` never runs a handler or a `JobRunner`. It advances the durable
  PipelineRun one short, idempotent step at a time under a row lock: it settles a
  finished task's Job, applies the quality gate, or creates, commits and dispatches the
  next task's Job. `JobRunner` reports a pipeline-owned Job's terminal state with one
  `advance` message.
- Celery is the only execution backend. The inline `PipelineRunner` /
  `PipelineTaskRunner` architecture and the Airflow backend (`pipeline_backend`,
  `AirflowPipelineExecutionBackend`, the DAGs, their Compose services and tests) are
  removed rather than kept as alternatives, so every guarantee has one implementation.
- Retry is an explicit redispatch of durable state, never a Celery-level replay of a
  message. A message carries an identifier only; PostgreSQL holds the state it refers
  to, so a duplicated or late message is harmless.

### 2. Durable state first

- A successful mutating API response represents committed PostgreSQL state:
  request-scoped mutations commit before the response is sent, and a failed commit is an
  error response.
- An operation that dispatches asynchronous work persists and commits first and sends
  the message second. A worker therefore never receives a reference to a row that is not
  durable, and a late `QUEUED` commit cannot overwrite a state a worker already advanced.

### 3. Derived artifacts are immutable and content-pinned

- Important derived bytes are written once at checksum-qualified keys, and an
  `ArtifactRecord` refers to those exact bytes and checksum. A record's id is derived
  from the logical artifact and its checksum.
- Retries and concurrent workers that produce identical content converge on the same
  object and record. Changed content is a new revision beside the old one. A record that
  disagrees with an existing one on kind, location, checksum or size fails loudly.

### 4. Acquisition keeps one capture shape

Capture is one one-shot process per `robot_run_id` and finalizes only on the run's
`RUN_END`. The continuous multi-run capture router is removed
([ADR-008](./008-acquisition-lifecycle-reliability.md), amendment). RobotRun remains the
single ingestion boundary, and Scene / Episode processing does not branch on acquisition
mode ([ADR-007](./007-canonical-ingestion-architecture.md)).

## Alternatives considered

- **Keep Airflow as an optional pipeline backend.** Rejected: it keeps two orchestration
  paths whose state transitions and quality-gate handling must stay equivalent, for a
  capability (scheduled, DAG-shaped execution) the platform does not use; dispatch is
  always an API call.
- **Keep executing a pipeline's Jobs inline in the pipeline process.** Rejected: the
  pipeline process stays an execution path beside `JobRunner`, holds a worker for the
  whole run, and cannot hand a Job to a worker pool.
- **Spend Celery retries on failures.** Rejected: a Job's outcome, failure included, is
  persisted by `JobRunner`, and a replayed message is not an explicit decision to run
  again.

## Consequences

- One execution path and one set of reliability tests (`SUITE=pipelines`).
- Pipeline tasks run strictly serially and the orchestrator is event-driven, so a lost
  `advance` message or a failed post-commit dispatch leaves a run waiting.
- There is no stall detection, worker-loss recovery, lease or heartbeat for Jobs and
  Pipelines; only `REGISTER_ROBOT_RUN` has stall handling, in the acquisition reconciler.
  Worker-loss recovery is decided in [ADR-010](./010-job-ownership-lease-and-fencing.md).
- Dispatch and the database are not one atomic unit, and `Celery.send_task` blocks the
  event loop where it is called from `async` code.
- There is no latest-revision lookup for a logical artifact and no artifact garbage
  collection.

These are current limitations, listed in
[Current limitations](../architecture/limitations.md); this ADR does not decide how to
close them.
