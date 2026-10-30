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

- **Worker loss and lost messages are recovered by a command, not by the workers.**
  Execution recovery (`sceneops-worker recover`, looped by `make recovery-up`) requeues
  a Job whose worker died once its lease passes (60 s), and re-sends a `run_job` or
  `advance` message that durable state has waited on for longer than its threshold
  (300 s); without that loop such a Job or PipelineRun waits indefinitely. A lease
  detects a dead or unreachable worker, not a handler that hangs in a live process, and
  a reclaimed handler's own domain writes are not fenced (they rely on idempotent
  publication).
- **Execution health is read on demand.** `make execution-status` derives backlog,
  queue age, latency percentiles, failures and recovery actions from PostgreSQL; no
  metric is exported, sampled or alerted on, and no worker capacity is recorded
  ([ADR-012](../adr/012-execution-health-from-durable-state.md)).
- **Delivery is at least once.** Recovery cannot tell a lost message from one waiting
  behind a backlog, so waiting Jobs receive one more message per threshold; consumers
  refuse the duplicates.
- **Dispatch and PostgreSQL are not one atomic unit**: a message can be sent without
  its `ExecutionRecord` committing, and an `ExecutionRecord` records a send, not a
  delivery.
- **The worker's broker sends are synchronous.** The worker's `ExecutionDispatcher`
  calls `Celery.send_task` from `async` code; the API's dispatch backends send on a
  thread instead. A worker task runs alone on its own event loop and renews its lease
  from another thread, so a slow broker delays only that task's step.
- **Pipeline tasks run strictly serially**, one Job in flight per run, in definition
  order; there is no parallel scheduling from the dependency graph.
- **No cancellation** of a Job or PipelineRun, and no scheduler.

## 2. Streaming and acquisition

Details: [Streaming transport](./streaming-transport.md) §26, §31 and
[Robot data ingestion](../workflows/robot-run-and-mcap.md) §3.2, §6.

- **A capture without a start position scans the whole topic.** Capture reads by
  assignment only and keeps no consumer-group progress
  ([ADR-013](../adr/013-capture-records-no-kafka-progress.md)). Given the run's
  `RUN_START` position (`--start-position`, which the streaming launcher takes from the
  bridge's `run_started` line), it reads that one partition from that offset; without
  one, every attempt, a re-run of a finished run included, reads every partition from
  the beginning to reach one run's records, and that cost grows with topic history. The
  position is not persisted: an operator re-run must supply it again or pay the scan.
  The scan measurements are single-broker, local-environment records in
  [History](../history/streaming-reliability-scale-baseline.md).
- **The Kafka replay window is a local-development setting.** The local broker keeps a
  run's records for at least 24 h and caps each partition at 20 GiB
  ([Streaming transport](./streaming-transport.md) §26.1); a failed capture must be
  re-run within that window, and a topic that reaches the cap loses it without a signal
  (none is built). A window for another deployment is derived from its ingest rate and
  recovery latency, not copied. No partition policy is configured: growing the partition
  count remaps future `robot_run_id` keys (§6).
- **Nothing supervises capture.** Capture is a one-shot process per `robot_run_id`; one
  that stops before `RUN_END` finalizes and commits nothing and is re-run by an
  operator. There is no multi-instance coordination, and capture-session state is not
  durable.
- **A run whose `RUN_END` never reached Kafka is recovered by hand.** Its records cannot
  complete it: every Capture of the run aborts on the idle guard and finalizes nothing
  ([Streaming transport](./streaming-transport.md), "Restart and failure behavior").
  Reconciliation and `make acquisition-status` classify it `capture_unfinished`
  (health `pending`, no operator action), the same as a capture still in progress, so
  only its age shows that it was abandoned; the streaming bootstrap's resume fails on
  it. The operator removes the run's `.partial` directory and streams the run again
  under the same `robot_run_id`. The aborted session stays in the topic until
  retention, so a later Capture of that run that starts before the new `RUN_START`
  (a scan without a start position) fails with `RunLifecycleError`; the new position
  is not persisted. Nothing removes a partial capture or re-streams automatically.
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
  derived revisions stay. Under `sources/` this includes files and inventories of an
  interrupted publication and descriptors whose registration was refused.
- **Preserved sources are moved whole through memory.** `publish-snapshot` reads,
  uploads and reads back one file at a time, and the first registration of a content
  reads every file back to hash it (8 at a time). The largest file measured is the
  6.9 MB pusht video; LeRobot v3 targets files of up to 500 MB, which would need
  streaming transfer. Measured on the local stack for the 0.48 GiB scene-0061 slice:
  publication 47–53 s, first registration ~4 s
  ([Source preservation verification](../history/source-preservation-verification.md)).
- **A source descriptor cannot be corrected.** One declaration with other annotations
  is a conflict; there is no correction or annotation record (ADR-015 OQ-10).
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

## 5. Canonicalization

Scenes are produced from a registered RobotRun recording or from a registered
nuScenes v1.0 SourceContent ([Scene domain](./scene-domain.md) §7). Episodes are
produced from a registered RobotRun recording or from a registered LeRobot v3.0
SourceContent ([Episode domain](./episode-domain.md)). No other external format has a
native reader or content builder, and no format has both. Current builder bounds:

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
- Units, revisions and DatasetVersion members are stored separately
  ([Data model](./data-model.md) §3). Canonical registration writes units and
  revisions only, and a DatasetVersion exists only as an immutable composition
  committed by a `COMPOSE_DATASET_VERSION` Job ([Data model](./data-model.md) §2.1).
  A recording pipeline composes only the revisions of its own single RobotRun and
  domain; a version of several RobotRuns or of both domains is a standalone
  composition. Registered units and revisions are never changed or removed.
- Validation and profiling address revisions by pin. Alignment and everything derived
  from it (aligned-episode keys, learning exports, curation) are scoped to the
  DatasetVersion they were produced for: an aligned revision of the same Episode
  revision is written again under each DatasetVersion that aligns it.
- A composition selector selects by source (a RobotRun or a SourceContent) and
  producer fingerprint only. A composition is one PostgreSQL transaction, whose commit-time membership check counts
  each version's members once; the largest composition tested has a handful of
  members, which is a test scope, not a supported maximum.
- `fixed_duration` windows are a grid anchored at the segmentation clock's zero, so the
  first and last window of a recording may be partial.
- Every recording clock has origin `source`, and no build configuration can assert an
  epoch, so timestamps of two RobotRuns are never comparable, even on the same clock id
  ([Ingress boundaries](./ingress-boundaries.md) §4.1). Consumers keep comparing clocks
  by id within one manifest; the origin instance is checked only through
  `require_comparable`, which no workflow calls yet.
- LeRobot content: only `codebase_version` v3.0 is read (v2.x is refused at build
  time, though such a snapshot is still preserved and registered). One data file and
  one task per episode are what the pinned pusht dataset exercises; multi-file and
  multi-task episodes follow the reader rules but have not been run against a real
  dataset. A dataset whose recorded timestamps are not exactly
  `float32(frame_index / fps)` is refused, never snapped. Values stay inline in the
  manifest: pusht Episode 0 is 201 KB and strict-loads in about 11 ms on a developer
  host. Video frames are referenced by a frame selector into the preserved MP4 and
  never decoded; frame-exact decoding (AV1 for pusht) is unverified.
- nuScenes content: only a `v1.0-<split>` table directory is read, and only the
  `scene-0061` slice (one scene of v1.0-mini with the shared tables) has been built on
  real data. Sensor readings, ego poses and samples are canonicalized; CAN and maps are
  preserved but not read, so a native nuScenes Scene has no CAN stream. Its annotation
  tables are read only by the native label import (a separate LabelSet, never part of
  the Scene). A slice snapshot that keeps the shared tables lists scenes
  whose files it does not preserve, so it is built with `unit_keys`. The scene-0061
  manifest is 2.9 MB (2,963 observations); readings are referenced as whole preserved
  files, never decoded at build time.
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
- `export_analytics_snapshot` covers Scene tables only. Observations are exported by
  payload reference (`payload_uri`, integrity, media type, `payload_selector`) for every
  source; the analytical tables carry no schema version of their own. Aligned Episodes
  have their own learning export.
- Native label import exists for nuScenes only (validated on the `scene-0061` slice);
  visibility, point counts and annotation prev / next links are not carried. An anchor
  carries no unit key, so a unit-origin clock cannot be labelled. Detection lifts
  2-D boxes only from `PointCloud2` CDR lidar payloads; a preserved nuScenes
  `.pcd.bin` reading is located but not lifted (a failed lift is recorded).
- Learning export is numeric scalar / vector only, with no lazy / streaming Torch
  dataset and no missing-value fill or mask policy
  ([Robot learning data](./robot-learning-data.md) §8,
  [Scalable learning data](./scalable-learning-data.md) §13).
- LeRobot is the only external export format; no integration-run record is
  persisted ([Dataset interoperability](./dataset-interoperability.md) §10,
  [External integration runtime](./external-integration-runtime.md) §4).
- Alignment refuses an Episode with a `transition` stream
  (`UnsupportedEpisodeContentError`), so a LeRobot Episode built with a `next.*`
  feature is not aligned or exported. Without one, alignment carries its video frames
  as reference values (Logical URI and `video_frame` selector; the learning export keeps
  the selector beside the URI) and decodes nothing; `ALIGN_EPISODE` is exercised on a
  real pusht Episode built without `next.*` (`SUITE=lerobot-episodes`), the learning and
  LeRobot exports have not been run on a LeRobot Episode. `frame_timeline`,
  `component_names` and `transition`
  streams have no consumer yet; an event view derived from a `transition` stream
  would need a declared rule (pusht's `next.done` is true on the last two frames of
  every episode).
- `JOB_STEP_DEFINITIONS_BY_TYPE` is declarative: only the first step's status is
  updated at runtime ([Jobs and pipelines](./jobs-and-pipelines.md) §6).

## 7. Product scope

- The platform is local-first and built to validate architecture, not large-scale
  throughput; the default fixture is nuScenes mini.
- GroundingDINO results are integration signals, not model benchmarks; the default
  Scene ML journey uses the mock backend.
- The GroundingDINO server reads camera images itself, from the physical `image_uri`
  (`file://` under its allowed roots, or `s3://` when S3 is enabled) and verifies the
  object's size and full sha256 against the pin in the request before decoding those
  bytes ([ADR-016](../adr/016-logical-uri-and-verified-payload-access.md)). A missing,
  mismatched or undecodable camera image fails the whole `predict_detection` run: the
  prediction manifest has no per-sample failure record, so it cannot be skipped the way an
  unreadable LiDAR payload only fails that sample's lifting.
- Operations and leaderboard APIs exist; there is no web UI.
