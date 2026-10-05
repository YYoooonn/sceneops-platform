# SceneOps Data Model

> Based on `packages/sceneops-db/sceneops_db/models/*.py` and
> `packages/sceneops-core/sceneops_core/*/schemas/`. Lists entity roles and
> the relationships/status values that matter; not every column — read the
> model files directly for full field lists.

## 1. Entity overview

```text
Dataset
 +- DatasetVersion

SceneRecord (scenes table)
 +- SceneRunRecord (validation / profile)

EpisodeRecord (episodes table)
 +- EpisodeRunRecord (validation / profile)

Robot
 +- RobotRun
     +- Mission
     +- RobotState (time series)

LabelSet (immutable revisions)
SceneSampleView (one revision per Scene revision and policy)
ScenarioSet (immutable revision pinning views)
 +- ScenarioRunRecord (mining / readiness)

PipelineRun
 +- PipelineTaskRun (order + declared-but-unused dependency graph)

Job
 +- JobEvent (execution log)

ExecutionRecord   (record of a Job/Pipeline actually sent to a backend)

InferenceRun (PredictionRun)
EvaluationRun

Artifact          (metadata index every domain's binary/JSON output goes through)
```

`SceneManifest` (with its `SceneLineage`) is an immutable Object Storage
artifact, not a table: `SceneRecord.manifest_artifact_id` pins the one
registered revision the row projects (see [Scene domain](./scene-domain.md)).
`EpisodeManifest` (with its `EpisodeLineage`) is likewise an immutable
artifact pinned by `EpisodeRecord.manifest_artifact_id` (see
[Episode domain](./episode-domain.md)). Prediction runs are `InferenceRunModel` in code (table `inference_runs`).

## 2. Dataset / DatasetVersion

`datasets` — a logical dataset (e.g. `nuscenes`). `dataset_id` is the PK,
plus a `default_version` pointer.

`dataset_versions` — one snapshot per version. `(dataset_id, version)` is
unique.

Key fields:
- `status`: `DatasetVersionStatus` — currently a single value, `registered`
  (see §2.1 below — this is intentional, not a placeholder).
- `scene_count` / `keyframe_count` / `observation_count` / `observed_channels`:
  the Scene membership summary, written only by Scene registration.
- `episode_count`: version-level Episode statistic (`EpisodeVersionSummary`
  — its own independent rollup, not derived from or overwritten by the
  Scene fields above; see the aggregate-summary contract below).

A DatasetVersion holds no channel requirements and no free-form metadata:
channel requirements are pipeline / job parameters (ADR-007 §16, I-59).

A DatasetVersion carries no source location or source format; it relates
to RobotRuns only through its units' provenance. `datasets` has no `type`.

A DatasetVersion caches no quality result. Scene and Episode readiness are
derived from run records (`scene_run_records` / `episode_run_records`),
for Scenes only from runs of each Scene's current manifest revision (see
[Quality and run records](./quality-and-runs.md)).

#### 2.0.1 The aggregate-summary contract

`SceneVersionSummary.scene_count` and `EpisodeVersionSummary.episode_count`
(`packages/sceneops-core/sceneops_core/datasets/schemas/summaries.py`) are
**cached aggregate projections of that DatasetVersion's current canonical
membership** — `scene_count` must always equal a live count of `SceneRecord`
rows owned by `(dataset_id, dataset_version)`, and `episode_count` a live
count of `EpisodeRecord` rows, at the moment each was last written. They are
**not** a metric of the latest ingest/build/register operation
(`registered_episode_count` on `RegisterEpisodeJobResult`, or the number of
scenes a single `build_recording_scenes` dispatch happened to build,
are the correct place for that — job results, never the DatasetVersion
summary).

Concretely, every writer recomputes from a live repository query
(`EpisodeRepository.count(...)`, `SceneRepository.summarize_membership(...)`)
rather than incrementing a delta onto the previous cached value — this is what keeps the count correct under retry,
replacement, duplicate input, and one registration per RobotRun: each
registration converges on the true total, not just what it touched. Episode
registration (`REGISTER_EPISODES`) is the sole writer of `episode_count`, in
the same transaction as the membership change; `build_recording_episodes`
only produces manifests, never an `EpisodeRecord`. Scene registration (`REGISTER_SCENES`) is the sole writer of the
Scene summary, in the same transaction as the membership change.

#### 2.0.2 Concurrency: serializing aggregate mutation per DatasetVersion

Recompute-from-live-count (§2.0.1) is correct for any single transaction,
but two independent transactions recomputing and writing the *same*
DatasetVersion's aggregate at overlapping times can still both compute a
now-stale count and both persist it — a classic lost update (e.g. two
concurrent Episode registrations for the same
`(dataset_id, dataset_version)`, each seeing only its own not-yet-committed
insert, both writing the same too-low count).

The fix is a real PostgreSQL row-level lock, not a distributed lock or
reconciliation process:
`PostgresDatasetVersionRepository.lock_for_update` issues
`SELECT ... FOR UPDATE` on the target DatasetVersion row. Both registrars
(`REGISTER_SCENES`, `REGISTER_EPISODES`) take it (via
`DatasetStore.lock_version_for_update`) before re-reading their recording
scope and before inserting any record. The order matters: inserting a
record takes a `FOR KEY SHARE` lock on the DatasetVersion row through its
foreign key, which a concurrent registration's `FOR UPDATE` would deadlock
against if the lock came second. A concurrent registration blocks at the
lock until the first commits, then observes its committed changes. Scene and Episode summaries occupy disjoint
columns (`replace_scene_membership_summary`/`update_episode_summary` each
write only their own domain's columns —
`values_without_none` + per-attribute `setattr` — so SQLAlchemy's
unit-of-work only marks the touched attributes dirty and emits an `UPDATE`
naming only those columns). Postgres's ordinary row-level write lock
already forces a concurrent Scene write and a concurrent Episode write to
the same row to serialize with each other without either losing its own
column's write, lock or no lock — proven by a genuine two-session
concurrency test, not just reasoned about
(`packages/sceneops-db/tests/test_episode_summary_aggregation.py`).

**Guarantee after this fix:** committed same-DatasetVersion Episode
aggregate mutations are fully serialized — the summary written by the last
transaction to commit always equals live canonical membership at that
point; the same holds for Scene registration. **Residual limitation:** this
guarantees correctness of *committed* state; it says nothing about a caller
observing a value mid-flight between two overlapping transactions
(ordinary READ COMMITTED behavior).

### 2.1 Why `DatasetVersionStatus` has one value

`DatasetVersionStatus` used to carry Scene-workflow-specific values
(`INGESTING`/`INGESTED`/`VALIDATING`/`PROFILING`/`READY`/`FAILED`/
`DEPRECATED`). All were removed — some had zero writers ever, the rest were
exclusively Scene-domain transitions that don't generalize to Episode (which
has its own, independent quality model). A `DatasetVersion` is now a
domain-agnostic parent record; per-domain processing state lives in that
domain's pipeline/job/run records and summary artifacts, not on this shared
row. Downstream readiness checks (e.g. "can I run detection prediction
against this version?") use explicit domain-scoped prerequisite functions
(`predict_detection.py`/`evaluate_detection.py`'s
`_require_scene_dataset_ready`) instead of branching on this field.

## 3. SceneRecord

`scenes` — canonical Scene membership; one row per registered Scene,
projecting the manifest revision `manifest_artifact_id` names. Written only
by Scene registration. There is no status column.

Key fields (contract: [Scene domain](./scene-domain.md) §2):
- `scene_id`: deterministic from `(dataset_id, dataset_version, robot_run_id, unit_key)`.
- `dataset_id`, `dataset_version`: FK to `dataset_versions` (RESTRICT).
- `robot_run_id` (NOT NULL, FK to `robot_runs`, RESTRICT), `unit_key`,
  `producer_fingerprint`: source and producer projections.
- `manifest_artifact_id` (FK to `artifacts`, RESTRICT), `manifest_checksum`:
  the current revision.
- `window_clock`, `window_start_timestamp_ns`, `window_end_timestamp_ns`:
  the segment window in the producer's declared segmentation clock (NOT
  NULL; `ck_scenes_segment_window` keeps it non-empty).
- `observed_channels`, `observation_count`, `keyframe_count`: searchable
  projections. A Scene embeds no annotations (labels are independent label
  sets), so there is no annotation count.

`scene_run_records` — unified scene-scope run table
(`scene_validation` / `scene_profile`). A per-scene row pins the revision it
assessed (`manifest_artifact_id` FK + `manifest_checksum`); a job-level
aggregate row has neither a scene nor a pin (CHECK constraint).

## 4. EpisodeRecord

`episodes` — canonical Episode membership: one row per registered Episode,
projecting the EpisodeManifest revision named by `manifest_artifact_id`.
Written only by the Episode registrar; no status, task or outcome column
(see [Episode domain](./episode-domain.md) §5).

Key fields:
- `(dataset_id, dataset_version)`: FK `dataset_versions`.
- `robot_run_id` (FK `robot_runs`), `unit_key`: the source projection.
- `producer_fingerprint`; `manifest_artifact_id` (FK `artifacts`) +
  `manifest_checksum`: the exact current revision.
- `window_clock`, `window_start_timestamp_ns`, `window_end_timestamp_ns`:
  the half-open window in the segmentation clock (CHECK non-empty).
- `observation_topics` / `state_topics` / `action_topics` / `event_topics`
  and the matching counts.

`episode_run_records` — unified episode-scope run table
(`episode_validation` / `episode_profile`), the same job-level/per-item
split as Scene's run records (`episode_id=None` -> aggregate across the
whole job; `episode_id` set -> a single episode, pinning the
`manifest_artifact_id` + `manifest_checksum` it assessed; CHECK
`ck_episode_run_records_revision_pin`).

## 5. Robot / RobotRun / Mission / RobotState

A separate domain from Dataset/Scene/Episode, for robot *runtime* data
rather than pre-recorded sensor datasets:

```text
Robot        static registry entry (robot_id, platform)
RobotRun     one finalized, published and verified recording (immutable provenance)
Mission      a run's lifecycle status (pending/running/completed/...), extracted from the bag
RobotState   a runtime-state time series (position, orientation, velocity, battery, ...)
```

`Robot` can be registered directly via `POST /robots`. A `RobotRun` comes
into existence only through `REGISTER_ROBOT_RUN` (ADR-007 §12), over a
recording the database-free Recording Publisher already published:

```text
finalized local MCAP
  -> Recording Publisher (sceneops_integrations.recording, no DB)
       {robot_run_root}/{run_id}/recording.mcap             write-once
       {robot_run_root}/{run_id}/robot_run_manifest.json    canonical RobotRunManifest v1, written last
  -> POST /robot-runs:register {manifest_uri}  ->  REGISTER_ROBOT_RUN Job
     (submitted by hand, or for every unregistered manifest by `reconcile --once --apply`)
       verify manifest (strict + canonical bytes) and recording (size, sha256, MCAP facts)
       one transaction: Robot create / platform fill-once,
                        ArtifactRecord(robot_run_recording), ArtifactRecord(robot_run_manifest),
                        RobotRunRecord
```

`robot_runs` columns: `run_id`, `robot_id` (FK `robots`, `ON DELETE
RESTRICT`), `started_at`, `ended_at`, `recording_format`, `source_clock`,
`recording_artifact_id`, `manifest_artifact_id` (both FK `artifacts`,
`ON DELETE RESTRICT`), `manifest_checksum`, `registered_at`. A
RobotRunRecord has no status, no dataset membership and no recording URI:
the recording URI/checksum/size live on its recording ArtifactRecord, and
channel/capture facts live only in the manifest. It is never updated, and
neither a Robot nor a RobotRun ArtifactRecord can be deleted while a
RobotRun references it. Re-registering the same manifest is a
no-op; a different manifest for an existing `run_id` fails. A manifest
`robot_platform` fills an empty `Robot.platform` once and fails registration
if it contradicts a set one.

`Mission`/`RobotState` are populated by the `ingest_robot_states` Job, which
reads a RobotRun's registered recording, resolved by `robot_run_id`, through
`RecordingTelemetryReader`. See
[Robot data ingestion](../workflows/robot-run-and-mcap.md) for the full
pipeline and current limitations.

These telemetry tables are a derived projection of the recording. Canonical
Episodes do not read them: `build_recording_episodes` reads the recording
itself through the shared recording reader, as configured by its build
configuration (see [Episode domain](./episode-domain.md)).

## 6. ScenarioSet

`scenario_sets` — one immutable ScenarioSet revision mined from a specific
`(dataset_id, dataset_version)`. It pins `manifest_artifact_id` and
`manifest_checksum` of its `ScenarioSetManifest`, which holds the members
(each pinning a sample view) and the explicit curation criteria; `tags`
classify it. Label sets and sample views have no dedicated tables: they are
checksum-pinned artifacts whose ArtifactRecords are the registry (see
[Derived layer](./derived-layer.md)).

`scenario_run_records` — two types, `scenario_mining` / `scenario_readiness`.
The readiness run carries dedicated aggregate columns
(`ready_count`/`blocked_count`/`warning_count`/`average_score`).

There is no per-scenario DB row; members live in the manifest
(see [Reserved architecture and current limitations](./reserved-and-limitations.md)).

## 7. PipelineRun / PipelineTaskRun

`pipeline_runs` — one pipeline execution. `type` is `PipelineType`:
`recording_scene_building`, `recording_episode_building`,
`scene_ml_evaluation`, `episode_learning_data_building`.

`pipeline_task_runs` — individual tasks inside a run. `task_order` gives
sequence; `depends_on_task_ids` (JSONB) declares dependencies but the
current `PipelineRunner` doesn't use that graph for scheduling — execution
is always strictly `task_order` sequential (see
[Jobs and pipelines](./jobs-and-pipelines.md)). `job_type`/`job_id` delegate
actual execution to a Job.

`PipelineRunStatus`: `pending -> queued -> running -> (blocked | succeeded
| failed | cancelled)`. `blocked` is what a quality gate produces when it
stops a pipeline mid-run.

## 8. Job / JobEvent

`jobs` — the actual unit of work. `type` is `JobType`:

```text
BUILD_RECORDING_SCENES                               # RobotRun recording -> canonical scenes
REGISTER_SCENES, VALIDATE_SCENE, PROFILE_SCENE         # scene-level
IMPORT_LABELS, BUILD_SCENE_SAMPLE_VIEWS                # derived: label set / sample view revisions
ALIGN_EPISODE, VALIDATE_ALIGNED_EPISODE, PROFILE_ALIGNED_EPISODE,
EXPORT_LEARNING_DATA, CURATE_EPISODES                  # derived: aligned episodes, learning export
MINE_SCENARIOS, SCORE_SCENARIO_READINESS               # scenario-level
EXPORT_ANALYTICS_SNAPSHOT                              # dataset-version-level
PREDICT_DETECTION, EVALUATE_DETECTION                  # detection
REGISTER_ROBOT_RUN                                     # published recording -> RobotRun, pipeline-less
INGEST_ROBOT_STATES, EXPORT_ROBOT_ANALYTICS_SNAPSHOT   # robot runtime, pipeline-less
BUILD_RECORDING_EPISODES, REGISTER_EPISODES            # RobotRun recording -> canonical episodes
VALIDATE_EPISODE, PROFILE_EPISODE                       # episode-level
```

`retry_count`/`max_retries`/`worker_id`/`queued_at`/`locked_at`/
`heartbeat_at` exist as columns, but `JobRunner` doesn't build its own
retry/backfill logic on top of them — only Celery-level retry runs today.
`JobService.mark_queued` does enforce `max_retries` for explicit
redispatch, returning a `ValueError` past the cap (see
[Jobs and pipelines](./jobs-and-pipelines.md) §5).

`job_events` — execution log/event stream for a job (`level`, `attempt`,
`job_step_id`, etc.).

Every `Job` also carries a `steps` list, populated at creation time from
`JOB_STEP_DEFINITIONS_BY_TYPE` — see [Jobs and pipelines](./jobs-and-pipelines.md)
§6 for what that metadata actually does (and doesn't do) at runtime.

## 9. InferenceRun (PredictionRun) / EvaluationRun

`inference_runs` — a model inference execution. `dataset_id` +
`dataset_version` + `model_id` + `model_version` pin down all four
reproducibility coordinates explicitly. `predictions_root_uri`,
`prediction_manifest_uri` locate the result and `prediction_manifest_checksum`
pins the exact prediction revision.

`evaluation_runs` — an evaluation execution. `inference_run_id` links back
to what was evaluated; `evaluator_id` (e.g. `center-distance`) and
`task_type` (e.g. `detection`) identify method. `primary_metric_name`/
`primary_metric_value` promote one headline metric; `class_metrics`
(JSONB) holds the rest.

## 10. Artifact

`artifacts` — the metadata index for every binary/JSON output in the
platform. `kind` (`ArtifactKind`), `uri`, `backend` identify what and
where; ownership is either the polymorphic `owner_type`/`owner_id` pair, or
one of the direct FK-style columns (`dataset_id`/`scene_id`/
`scenario_set_id`/`run_id`/`job_id`/`pipeline_run_id`). `size_bytes`/
`checksum` exist as columns but most writers don't populate them — see
[Storage layout](./storage-layout.md) §6.

See [Artifact ownership and lineage](./jobs-and-pipelines.md#7-artifact-ownership-invariant)
for the "producer owns the ArtifactRecord" invariant and how Scene/Episode
apply it.

## 11. ExecutionRecord

`execution_records` — the record of a Job/Pipeline actually sent to a real
execution backend (Celery today, Airflow for pipelines). `execution_backend`,
`execution_kind`, `resource_id` (a `job_id` or `pipeline_run_id`),
`external_id` (the Celery task id) separate the control plane's logical
resource from the physical execution. This is the abstraction point future
backends attach to — see [ADR-004](../adr/004-airflow-vs-celery.md).
