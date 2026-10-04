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

ScenarioSet
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
Episode keeps `EpisodeRecord.episode_manifest_uri` + `EpisodeLineage`
embedded inside the manifest rather than the DB row (see
[Episode domain](./episode-domain.md) §3). Prediction runs are `InferenceRunModel` in code (table `inference_runs`).

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
- `required_channels` (validation default), `manifest_uri` (derived dataset
  manifest location): Scene inputs that are not membership, patched through
  `update_scene_inputs`.

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
upsert, duplicate input, and independent per-scene dispatches (as
`scripts/canonical/canonical_bootstrap.sh` performs one `register_episode`
dispatch per source scene): each dispatch converges on the true total, not
just what that one dispatch touched. `RegisterEpisodeJobHandler` is the sole
production writer of `episode_count` (it runs after `BuildEpisodesJobHandler`,
which only produces manifests — never an `EpisodeRecord` — so writing the
summary any earlier would count something that doesn't canonically exist
yet). Scene registration (`REGISTER_SCENES`) is the sole writer of the
Scene summary, in the same transaction as the membership change.

#### 2.0.2 Concurrency: serializing aggregate mutation per DatasetVersion

Recompute-from-live-count (§2.0.1) is correct for any single transaction,
but two independent transactions recomputing and writing the *same*
DatasetVersion's aggregate at overlapping times can still both compute a
now-stale count and both persist it — a classic lost update (e.g. two
concurrent `register_episode` dispatches for the same
`(dataset_id, dataset_version)`, each seeing only its own not-yet-committed
insert, both writing the same too-low count).

The fix is a real PostgreSQL row-level lock, not a distributed lock or
reconciliation process:
`PostgresDatasetVersionRepository.lock_for_update` issues
`SELECT ... FOR UPDATE` on the target DatasetVersion row, and
`RegisterEpisodeJobHandler` acquires it (via
`DatasetStore.lock_version_for_update`) immediately before its
count-then-write pair, for every `(dataset_id, dataset_version)` it
touched, in sorted order (a consistent lock-acquisition order across
concurrent dispatches, so two dispatches that each touch more than one
DatasetVersion can never deadlock against each other). A concurrent
dispatch's own lock acquisition blocks until the first commits, and then
observes that transaction's fully committed changes — turning "recompute,
then write" into a real serialization point for the affected row.

Scene registration takes the same lock before re-reading its scope and
recomputing the Scene summary. Scene and Episode summaries occupy disjoint
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
- `observed_channels`, `observation_count`, `keyframe_count`,
  `annotation_count`: searchable projections.

`scene_run_records` — unified scene-scope run table
(`scene_validation` / `scene_profile`). A per-scene row pins the revision it
assessed (`manifest_artifact_id` FK + `manifest_checksum`); a job-level
aggregate row has neither a scene nor a pin (CHECK constraint).

## 4. EpisodeRecord

`episodes` — the canonical Episode-domain unit: one task-oriented
observation+action window, segmented from a robot recording.

Key fields:
- `raw_log_id`, `robot_id`, `robot_run_id`, `mission_id`: plain indexed
  lineage columns back into the raw-log/robot domains — not foreign keys
  (the referenced row may live in a table this domain doesn't own, same
  convention as `RobotStateModel.scene_id`).
- `status`: `EpisodeStatus` — `created`/`registered` only. See
  [Episode domain](./episode-domain.md) §4 for why this stays a
  registration-lifecycle field and never encodes quality.
- `task`, `outcome` (`EpisodeOutcome`: success/failure/unknown).
- `episode_manifest_uri`: ArtifactStore reference.
- `observation_channels` / `action_channels` / `control_frequency_hz`:
  what the episode actually contains.
- `frame_count`, `started_at`, `ended_at`.

`episode_run_records` — unified episode-scope run table
(`episode_validation` / `episode_profile`), the same job-level/per-item
split as Scene's run records (`episode_id=None` -> aggregate across the
whole job; `episode_id` set -> a single episode).

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
`RosbagAdapter`. See
[Robot data ingestion](../workflows/robot-run-and-mcap.md) for the full
pipeline and current limitations.

Episode build (`build_episodes`) reads the *same* `RosbagAdapter` output
(robot states + missions + sensor frames) as `ingest_robot_states` does, but
through a separate `EpisodeSource` read (see
[Episode domain](./episode-domain.md) §2) — the two Job types don't share a
DB table and can run independently of each other.

## 6. ScenarioSet

`scenario_sets` — a curated bundle of scenarios mined from a specific
`(dataset_id, dataset_version)`. `scenario_set_uri` references the actual
candidate list; `tags` classify it.

`scenario_run_records` — two types, `scenario_mining` / `scenario_readiness`.
The readiness run carries dedicated aggregate columns
(`ready_count`/`blocked_count`/`warning_count`/`average_score`).

Each scenario candidate inside the artifact carries a `ScenarioStatus`
(`candidate`/`selected`/`rejected`/`exported`/`deprecated`) — this is
artifact-level state, not a DB column; there is currently no per-scenario
DB row (see [Reserved architecture and current limitations](./reserved-and-limitations.md)).

## 7. PipelineRun / PipelineTaskRun

`pipeline_runs` — one pipeline execution. `type` is `PipelineType`:
`recording_scene_building`, `scenario_curation`, `detection_evaluation`,
`raw_log_episode_building`.

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
BUILD_DATASET_MANIFEST, BUILD_SCENE_INDEX             # dataset-level aggregation
REGISTER_SCENES, VALIDATE_SCENE, PROFILE_SCENE         # scene-level
COMPARE_SCENES, AUTO_LABEL_SCENE, EXPORT_SCENE_PACKAGE # scene-level, reserved (no handler)
MINE_SCENARIOS, SCORE_SCENARIO_READINESS               # scenario-level
AUTO_LABEL_DATASET, EXPORT_DATASET                     # dataset-version-level, reserved (no handler)
EXPORT_ANALYTICS_SNAPSHOT                              # dataset-version-level
PREDICT_DETECTION, EVALUATE_DETECTION                  # detection
REGISTER_ROBOT_RUN                                     # published recording -> RobotRun, pipeline-less
INGEST_ROBOT_STATES, EXPORT_ROBOT_ANALYTICS_SNAPSHOT   # robot runtime, pipeline-less
BUILD_EPISODES, REGISTER_EPISODE                        # robot rosbag/MCAP -> episode
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
`prediction_manifest_uri` locate the result.

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
