# Current Limitations

What the platform does not do today, verified against the code. This is not a
roadmap: an entry here is a constraint a developer or operator must account for, not
a commitment to build anything. A capability that was removed from the code is not
listed as a limitation or a plan; [Overview](./overview.md) §7 states what is not part
of the runtime.

Where a subsystem document already owns its limitations, this page names them in one
line and links there instead of repeating the contract.

## 1. Execution

Details and failure windows: [Jobs and pipelines](./jobs-and-pipelines.md) §5, §8.

- **Worker loss is recovered by a command, not by the workers.** A Job whose worker
  died is requeued by job lease recovery (`sceneops-worker jobs recover-leases`, looped
  by `make recovery-up`) once its lease passes; without that loop it stays `RUNNING`,
  and so does its PipelineRun. A lease detects a dead or unreachable worker, not a
  handler that hangs in a live process, and a reclaimed handler's own domain writes are
  not fenced (they rely on idempotent publication).
- **A lost `advance` message, or a Job dispatch that fails after its commit, is not
  re-sent.** The run waits until a person takes an `advance` step
  (`make worker-advance-pipeline`) or dispatches the Job.
- **Dispatch and PostgreSQL are not one atomic unit**: a message can be sent without
  its `ExecutionRecord` committing.
- **`Celery.send_task` blocks the event loop** in the API's dispatch backends and in
  the worker's dispatcher, which both call it from `async` code.
- **Pipeline tasks run strictly serially**, one Job in flight per run, in definition
  order; there is no parallel scheduling from the dependency graph.
- **No cancellation** of a Job or PipelineRun, and no scheduler.

## 2. Streaming and acquisition

Details: [Streaming transport](./streaming-transport.md) §26, §31 and
[Robot data ingestion](../workflows/robot-run-and-mcap.md) §3.2, §6.

- **Capture replays the whole topic.** Each capture uses its own consumer group with
  `auto.offset.reset=earliest`, so it reads the topic from the beginning to reach one
  run's records. The cost grows with topic history; the measurements are
  single-broker, local-environment records in
  [History](../history/streaming-reliability-scale-baseline.md).
- **No Kafka retention or partition policy is configured.** The local broker
  auto-creates `sceneops.robot.telemetry.v1` with broker defaults; a run's records must
  still be on the topic when its capture runs, and growing the partition count remaps
  future `robot_run_id` keys ([Streaming transport](./streaming-transport.md) §6).
- **Nothing supervises capture.** Capture is a one-shot process per `robot_run_id`; one
  that stops before `RUN_END` finalizes and commits nothing and is re-run by an
  operator. There is no multi-instance coordination, and capture-session state is not
  durable.
- **Capture does not trigger publication or registration.** `publish-pending` and
  `reconcile --once --apply` must be invoked (`make recovery-up` loops them locally).
  Legacy finalized bags without a receipt, conflicts, integrity incidents and a
  registration that has spent its attempt budget are operator decisions.
- **The registration stall threshold (900 s) was measured on one host with local
  storage.**
- **Kafka messages are limited to the stock ~1 MB size**; larger payloads are not
  configured.
- **No live robot control.** Acquisition ends at a registered RobotRun; there is no
  command path back to a robot ([ADR-005](../adr/005-ros2-vs-kafka-boundary.md)).
- **Recordings are read whole** by the publisher, registration and the resolver.

## 3. Artifacts and storage

- **No artifact deletion, quarantine or garbage collection.** `ArtifactStore` has a
  `delete_prefix` operation, but no application code path calls it. Objects under
  `robot_runs/` are classified (`referenced` / `pending` / `orphan_candidate` /
  `integrity_incident`) and nothing else; no other prefix is classified, and superseded
  derived revisions stay.
- **No "latest revision of a logical artifact" lookup.** A derived artifact is a
  checksum-pinned revision, and consumers resolve a pin they were given. Nothing
  answers "the newest revision of this logical artifact"; because a revision is never
  overwritten, a caller that wants the newest must choose and pin it.
- **DuckDB queries read only local Parquet files.** Querying MinIO / S3 objects
  directly is not configured; the supported path downloads first.

## 4. Observability

There is no metrics export (Prometheus, OpenTelemetry) and no performance
instrumentation. The structured recovery log lines and the read-only reports
(`make acquisition-status`, `make artifact-lifecycle-once`) are the operational
interface, and nothing stores them.

## 5. Canonicalization from recordings

Scenes and Episodes are produced only from a registered RobotRun recording. Current
builder bounds:

- Only ROS 2 (`cdr` / `ros2msg`) messages are decoded. Scene camera payloads must be
  `CompressedImage` (jpeg / png); other Scene channels are stored as their serialized
  ROS 2 message. Detection decodes lidar from `PointCloud2` CDR only.
- Scene calibration is constant for the whole recording, relative to the ego frame, and
  representable (no distortion, identity rectification). Scene segmentation is
  `whole_recording` or `fixed_duration`.
- Episode segmentation is `whole_recording`, `fixed_duration` or `event_markers`; an
  unterminated task (start marker without end marker) fails the build. A selected
  field must resolve to a bool, integer, finite float, string or numeric list in every
  message; NaN / Infinity fail the build. Canonical Episodes carry no outcome, success
  label, reward or language instruction.
- The robot telemetry projection (`ingest_robot_states`) reads its own fixed topic set
  and `RobotStateRecord` columns; it is a derived table, not canonical Episode data.
- Validation scope: the journeys are validated on one nuScenes mini scene
  (`scene-0061`) and the reference corpus of
  [Reference corpus](../development/reference-corpus.md); this is a measurement scope,
  not a supported maximum.

## 6. Derived layer and learning data

- Scenario members live in the ScenarioSet manifest; there is no per-scenario table or
  queryable review status. Readiness scoring uses label counts, channels and Scene
  readiness, not image or LiDAR content.
- Sample views associate by nearest / previous only (no pose interpolation); evaluation
  applies no frame transform. Episodes have no label sets. See
  [Derived layer](./derived-layer.md) §7.
- `export_analytics_snapshot` covers Scene tables only; aligned Episodes have their own
  learning export.
- Learning export is numeric scalar / vector only, with no lazy / streaming Torch
  dataset and no missing-value fill or mask policy
  ([Robot learning data](./robot-learning-data.md) §8,
  [Scalable learning data](./scalable-learning-data.md) §13).
- LeRobot is the only external format and is export-only; no integration-run record is
  persisted ([Dataset interoperability](./dataset-interoperability.md) §10,
  [External integration runtime](./external-integration-runtime.md) §4).
- `JOB_STEP_DEFINITIONS_BY_TYPE` is declarative: only the first step's status is
  updated at runtime ([Jobs and pipelines](./jobs-and-pipelines.md) §6).
- `DatasetVersionStatus` has exactly one value, `registered`, by design: processing
  state lives in Job, Pipeline and run records ([Data model](./data-model.md) §2.1).

## 7. Product scope

- The platform is local-first and built to validate architecture, not large-scale
  throughput; the default fixture is nuScenes mini.
- GroundingDINO results are integration signals, not model benchmarks; the default
  Scene ML journey uses the mock backend.
- Operations and leaderboard APIs exist; there is no web UI.
