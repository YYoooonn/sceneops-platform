# Reserved Architecture and Current Limitations

Two purposes for this doc: (1) tell you what looks unwired but is
*intentionally* reserved, so it isn't mistaken for dead code during a future
cleanup pass, and (2) give a single, verified list of what the platform
currently does not do. Everything here was confirmed against the code as of
Stabilization Requests 1–5 — not aspirational.

## 1. Reserved JobTypes

Five `JobType` values have no registered handler in
`create_default_job_handler_registry()`:

```text
COMPARE_SCENES, AUTO_LABEL_SCENE, EXPORT_SCENE_PACKAGE   # scene-level
AUTO_LABEL_DATASET, EXPORT_DATASET                        # dataset-version-level
```

Each is retained specifically because it has a matching, otherwise-unused
`ArtifactOwnerType` or `ArtifactKind` reservation already defined
(`SCENE_COMPARISON_RUN`/`SCENE_AUTO_LABEL_RUN`/`SCENE_EXPORT_RUN` +
`SCENE_PACKAGE`; `DATASET_AUTO_LABEL_RUN`/`DATASET_EXPORT_RUN`) — concrete
evidence of a deliberate reservation, not accidental drift. This is the
standard applied during Stabilization Request 5's cleanup: a sixth
handler-less `JobType`, `CHECK_DISTRIBUTION`, had no such matching evidence
and was removed entirely, along with its now-orphaned
`ArtifactOwnerType.DATASET_DISTRIBUTION_RUN`/`ArtifactKind.DISTRIBUTION_REPORT`.
`apps/worker/tests/jobs/test_job_registry.py`'s
`test_orphan_job_types_are_exactly_the_documented_reservations` locks this
exact set in — if it starts failing because a *new* orphan appeared, that's
a real regression, not a reason to widen the set.

Two other handler-registered JobTypes, `INGEST_ROBOT_STATES` and
`EXPORT_ROBOT_ANALYTICS_SNAPSHOT`, are intentionally absent from every
pipeline definition — they're dispatched as standalone Jobs, not through a
pipeline (see [Jobs and pipelines](./jobs-and-pipelines.md) §2). This is
different from the reservation above: these two are fully implemented,
just pipeline-less by design.

The five Phase 2 robot-learning-data JobTypes (`ALIGN_EPISODE`,
`VALIDATE_ALIGNED_EPISODE`, `PROFILE_ALIGNED_EPISODE`,
`EXPORT_LEARNING_DATA`, `CURATE_EPISODES`) follow the same pattern — fully
implemented, handler-registered, but not wired into
`RAW_LOG_EPISODE_BUILDING_PIPELINE` or any other pipeline. See
[Robot learning data layer](./robot-learning-data.md) §8.

## 2. WORLD_STATE (Scene)

`packages/sceneops-core/sceneops_core/scenes/schemas/world_state.py` defines
a complete, coherent schema for richer 3D scene reconstruction (scene
graph, physics bodies, static/dynamic assets) beyond today's flat
`SceneManifest`. As of Stabilization Request 5's audit: no job handler
reads or writes any of it — `build_scenes.py` never branches on the
`build_world_state` param that threads through pipeline/job schemas
(defaults to `False` everywhere), no persisted `SceneRecord` has
`world_state_manifest_uri` set, and `SceneManifest.world_state` is never
populated.

It's retained rather than removed because it's a complete, working schema
with zero compatibility cost — everything defaults off, nothing depends on
it — not confused or abandoned code left over from a removed feature. If
it's still unimplemented by the time a broader architecture pass happens,
that's the point to decide whether to build it or finally drop it.

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
- Scenario curation is implemented but still `experimental=True`; scenario
  candidates are artifact-backed only (§1 of
  [Quality and run records](./quality-and-runs.md) — no per-scenario DB
  row), and readiness scoring uses metadata/scene-quality signals, not
  image/LiDAR content.
- The reserved JobTypes in §1 (scene comparison, auto-labeling, scene
  package export, dataset auto-labeling, dataset export) are defined but
  not implemented.
- Operations and leaderboard APIs exist; there's no dedicated web UI.
- The Airflow pipeline backend is a per-task DAG proof of concept
  hardcoded to `dataset_scene_ingestion` — every other pipeline type,
  including `raw_log_episode_building`, only runs through Celery.
- Episode still has no `DatasetManifest`-equivalent aggregate index, and
  `export_analytics_snapshot` still covers Scene only — but as of Phase 2,
  aligned Episode revisions do get their own Parquet export
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
  intentional v1 boundaries — no remote Parquet predicate pushdown, no
  lazy/streaming Torch dataset, numeric-only dense projection, no
  missing-value fill/mask policy — see
  [Robot learning data layer](./robot-learning-data.md) §8 for the full,
  verified list.
- Phase 3's dataset interoperability layer (the `ExternalDatasetAdapter`
  contract and its one concrete LeRobot implementation) has its own
  intentional v1 boundaries — numeric-only, no image/video, no RLDS
  adapter yet, no persistent SceneOps record for an external export, and
  a LeRobot runtime that is permanently isolated from the main workspace
  by a real dependency conflict (not a temporary inconvenience) — see
  [Dataset interoperability](./dataset-interoperability.md) §10 for the
  full, verified list.
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
- Live robot control or real-time telemetry streaming (robot data
  ingestion today is strictly batch: replay -> record -> decode -> ingest;
  see [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) for the intended
  future boundary once a streaming path is built).
- Evaluation-aware scenario mining (FP/FN-by-scene signals), pseudo-label
  candidate scoring, or VLM-based scene tagging.
- An RLDS (or any other second) external training-format adapter —
  `ExternalDatasetAdapter`/`ExternalDatasetWriter` are already
  format-neutral and support this without redesign, but none exists yet.
  A LeRobot adapter *is* implemented (Phase 3, complete) — see
  [Dataset interoperability](./dataset-interoperability.md).
- Containerized/portable packaging of the LeRobot integration runtime
  (`tools/lerobot-integration/` is a local uv project only today) —
  scoped to Phase 4 ("External Integration Runtime"), not started. See
  [Dataset interoperability](./dataset-interoperability.md) §11.
