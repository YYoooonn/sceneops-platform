# Reserved Architecture and Current Limitations

Two purposes for this doc: (1) tell you what looks unwired but is
*intentionally* reserved, so it isn't mistaken for dead code during a future
cleanup pass, and (2) give a single, verified list of what the platform
currently does not do. Everything here was confirmed against the code as of
Stabilization Requests 1–5 — not aspirational.

## 1. Pipeline-less JobTypes

Every `JobType` has a registered handler
(`apps/worker/tests/jobs/test_job_registry.py` locks this in). Some are
intentionally absent from every pipeline definition and are dispatched as
standalone Jobs:

- `INGEST_ROBOT_STATES`, `EXPORT_ROBOT_ANALYTICS_SNAPSHOT`,
  `REGISTER_ROBOT_RUN` — robot runtime, separate from Dataset/DatasetVersion
  (see [Jobs and pipelines](./jobs-and-pipelines.md) §2);
- `IMPORT_LABELS`, `VALIDATE_ALIGNED_EPISODE`, `PROFILE_ALIGNED_EPISODE` —
  single-stage derived operations (see [Derived layer](./derived-layer.md)).
  `BUILD_SCENE_SAMPLE_VIEWS`, `ALIGN_EPISODE`, `EXPORT_LEARNING_DATA` and the
  curation / prediction / evaluation jobs are also dispatchable alone, and are
  stages of the `scene_ml_evaluation` and `episode_learning_data_building`
  pipelines;
- `CURATE_EPISODES` — see [Robot learning data layer](./robot-learning-data.md) §8.

Scene comparison, auto-labeling, scene package export and dataset export have
no JobType; auto-labels would be ordinary label sets with `model` provenance.

## 2. Scenes from recordings only

Canonical Scenes are produced only by `RECORDING_SCENE_BUILDING` from a
registered RobotRun recording ([Scene domain](./scene-domain.md) §6). In
the v1 builder:

- only ROS 2 (`cdr` / `ros2msg`) recordings are decoded; camera payloads
  must be `CompressedImage` (jpeg / png), other channels are stored as
  their serialized ROS 2 message;
- calibration must be constant for the whole recording, relative to the
  ego frame, and representable (no distortion, identity rectification);
- segmentation is `fixed_duration` only;
- recording-derived Scenes carry no annotations or keyframe groups; labels
  are separate label sets and synchronization lives in sample views
  ([Derived layer](./derived-layer.md));
- the recording is materialized in memory by `resolve_recording` (whole
  bytes, no streaming read); measured with one nuScenes mini scene
  (~356 MB MCAP) only.

Detection locates payloads through their ArtifactRecords and decodes lidar
by declared media type; only `PointCloud2` CDR is supported. Frustum lifting
reads payloads through the ArtifactStore.

## 2a. Episodes from recordings only

Canonical Episodes are produced only by `RECORDING_EPISODE_BUILDING` from a
registered RobotRun recording ([Episode domain](./episode-domain.md)). In
the v1 builder:

- only ROS 2 (`cdr` / `ros2msg`) messages are decoded; `json_string`
  decoding reads a JSON object from a `std_msgs/msg/String`;
- a selected field must resolve to a bool, integer, finite float, string or
  numeric list in every message; NaN / Infinity fail the build;
- segmentation is `whole_recording`, `fixed_duration` or `event_markers`; an
  unterminated task (start marker without end marker) fails the build;
- canonical Episodes carry no outcome, success label, reward or language
  instruction; `AlignedEpisode.task` / `outcome` stay empty because label
  sets are defined for Scene observations only;
- the robot telemetry projection (`ingest_robot_states`,
  `RecordingTelemetryReader`)
  still reads its own fixed topic set and `RobotStateRecord` columns
  (`steering` / `throttle` / `brake`, ...) — a derived table, not canonical
  Episode data;
- `make e2e-batch-canonical` is the Episode (and Scene) canonicalization
  journey and `make e2e-episode-learning` the aligned / export journey.

## 3. `DatasetVersionStatus`: intentionally minimal

`DatasetVersionStatus` currently has exactly one value, `registered`. This
is not an in-progress migration — see
[Data model](./data-model.md) §2.1 for why a shared, domain-agnostic parent
record deliberately stopped trying to represent Scene-specific processing
states, and why that state lives in domain-scoped pipeline/job/run records
instead.

## 4. `JOB_STEP_DEFINITIONS_BY_TYPE`: declarative metadata, not live progress

Covered in full in [Jobs and pipelines](./jobs-and-pipelines.md) §6 — a
per-`JobType` list of named steps is created with every `Job`, but only the
first step's status is ever updated by the runtime. Treat it as UI-facing
documentation of a job's internal stages, not a progress tracker, unless
someone wires per-step reporting into handlers.

## 5. `ScenarioStatus`: documented only to its current extent

See [Quality and run records](./quality-and-runs.md) §4. `EXPORTED` and
`DEPRECATED` exist in the enum but nothing writes them today — their
presence doesn't imply an export or deprecation workflow exists.

## 6. Current limitations (verified, not a roadmap)

- The default local dataset is nuScenes mini.
- The platform is local-first, built to validate architecture rather than
  for large-scale production throughput.
- GroundingDINO evaluation results are integration signals, not production
  model benchmarks.
- Scenario members are manifest-backed only (no per-scenario DB row), and readiness
  scoring uses label counts, channels and Scene readiness, not image/LiDAR
  content.
- Sample views associate by nearest / previous only (no pose interpolation);
  evaluation applies no frame transform. See [Derived layer](./derived-layer.md) §7.
- Scene comparison, auto-labeling, scene package export and dataset export
  are not implemented.
- Operations and leaderboard APIs exist; there's no dedicated web UI.
- The Airflow pipeline backend is a per-task DAG proof of concept: one DAG per
  pipeline type, serial tasks, the API backend chosen at process start. Its
  acceptance (`make test-infrastructure-airflow`) is opt-in.
- `export_analytics_snapshot` covers Scene only; aligned Episode revisions have their own Parquet export
  (`EXPORT_LEARNING_DATA` -> `learning_episodes/steps/signals.parquet`,
  scoped by `(dataset_id, dataset_version, export_id)`, not by
  `export_analytics_snapshot`) and their own selectable-by-quality concept
  (`CURATE_EPISODES` -> `EpisodeCurationManifest.selected_aligned_artifact_
  checksums`) — see [Robot learning data layer](./robot-learning-data.md).
- Robot data ingestion limitations (binary sensor payload capture,
  `/vehicle/control`+`/mission/status` message bridging, pre-registration
  flow) — see [Robot data ingestion](../workflows/robot-run-and-mcap.md) §6.
- `Artifact.checksum`/`size_bytes` aren't populated by most writers — see
  [Storage layout](./storage-layout.md) §6.
- Phase 2's robot learning data layer (alignment through
  `SceneOpsDataset`/`SequenceSampler`/consumer adapters) has its own
  intentional boundaries — no lazy/streaming Torch dataset, numeric-only
  dense projection, no missing-value fill/mask policy — see
  [Robot learning data layer](./robot-learning-data.md) §8 for the full,
  verified list. (Remote Parquet predicate pushdown was added by Phase 5
  for the production sharded layout — selective/shard-aware-bulk reads via
  `ArtifactStore.read_range` — and remains absent only on the legacy
  single-file golden-fixture path; see
  [Scalable learning data](./scalable-learning-data.md) §5.)
- Phase 3's dataset interoperability layer (the `ExternalDatasetAdapter`
  contract and its one concrete LeRobot implementation) has its own
  intentional v1 boundaries — numeric-only, no image/video, no RLDS
  adapter yet, no persistent SceneOps record for an external export — see
  [Dataset interoperability](./dataset-interoperability.md) §10 for the
  full, verified list.
- Phase 4's external integration runtime layer (`IntegrationExecutor`, the
  isolated nuScenes/LeRobot runtimes) has its own intentional v1
  boundaries — LeRobot is not yet worker-invoked via HTTP (only nuScenes
  is), `ContainerIntegrationExecutor` is local/dev-only, no Kubernetes
  executor, no service discovery/plugin registry, no persistent
  integration-run DB entity, `POST /execute` is synchronous only — see
  [External integration runtime](./external-integration-runtime.md) §8
  for the full, verified list.
- DuckDB queries only work against locally-downloaded Parquet files —
  querying MinIO/S3-backed artifacts directly would need DuckDB's
  httpfs/S3 extension, not wired up.

## 7. Non-goals (for now)

Not because they're bad ideas — because nothing in the current codebase
implements or half-implements them, so there's nothing to document as
"current state":

- Temporal (or any other durable-workflow engine) as a pipeline backend.
- A per-scenario item table / queryable review status for scenario
  candidates.
- Live robot control. Telemetry and sensor streaming exists as an
  acquisition mode (ROS2 -> bridge -> Kafka -> capture -> L1 MCAP, see
  [streaming-transport.md](./streaming-transport.md)), and canonicalization
  is always a batch job over a registered recording; command/control back to
  a robot is not built (see [ADR-005](../adr/005-ros2-vs-kafka-boundary.md)).
- Automatic capture -> publish -> register hand-off, durable recovery of
  in-flight capture sessions across a restart, and Kafka message sizes
  beyond the stock ~1 MB limit (streaming-transport §14, §26, §33). Where a
  run stands is observable (`reconcile --once`, read-only; see
  [Robot run and MCAP](../workflows/robot-run-and-mcap.md) §3.2), but
  nothing acts on it: publishing, registration submission and stalled-Job
  handling remain explicit operator steps.
- Evaluation-aware scenario mining (FP/FN-by-scene signals), pseudo-label
  candidate scoring, or VLM-based scene tagging.
- An RLDS (or any other second) external training-format adapter —
  `ExternalDatasetAdapter`/`ExternalDatasetWriter` are already
  format-neutral and support this without redesign, but none exists yet.
  A LeRobot adapter *is* implemented (Phase 3, complete) — see
  [Dataset interoperability](./dataset-interoperability.md).
- A Kubernetes (or other remote-cluster) `IntegrationExecutor` backend —
  Phase 4 built the Protocol to make one a drop-in addition, but none
  exists. A service-discovery/plugin registry for integration runtimes,
  similarly — routing stays explicit per-integration config by design.
  See [External integration runtime](./external-integration-runtime.md) §8.
