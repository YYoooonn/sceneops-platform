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

`SceneManifest` and `SceneLineage`, as separate concepts, don't exist as
their own tables — they're fields on `SceneRecord`
(`scene_manifest_uri`, `lineage` JSONB). Episode follows the same pattern:
`EpisodeRecord.episode_manifest_uri` + `EpisodeLineage` embedded inside the
manifest rather than the DB row (see [Episode domain](./episode-domain.md)
§3). Prediction runs are `InferenceRunModel` in code (table `inference_runs`).

## 2. Dataset / DatasetVersion

`datasets` — a logical dataset (e.g. `nuscenes`). `dataset_id` is the PK,
plus a `default_version` pointer.

`dataset_versions` — one snapshot per version. `(dataset_id, version)` is
unique.

Key fields:
- `status`: `DatasetVersionStatus` — currently a single value, `registered`
  (see §2.1 below — this is intentional, not a placeholder).
- `scene_count` / `sample_count` / `frame_count`: version-level statistics
  (Scene-domain only).
- `episode_count`: version-level Episode statistic (`EpisodeVersionSummary`
  — its own independent rollup, not derived from or overwritten by the
  Scene fields above; see the aggregate-summary contract below).
- `channels` / `required_channels`: sensor channel lists (JSONB).
- `manifest_uri`, `raw_source_root_uri`: ArtifactStore references.
- `latest_validation_run_id` / `validation_status` / `should_block_pipeline`
  / `validation_report_uri`: cached back-reference to the latest Scene
  VALIDATE result.
- `latest_profile_run_id` / `profile_report_uri`: same pattern, for PROFILE.

`DatasetVersion` caches the latest Scene-domain quality-run result so "is
this version usable?" doesn't require joining `scene_run_records` on every
read. Actual validate/profile execution history lives at scene scope
(`scene_run_records`, §3), never at dataset scope — dataset-scoped
`dataset_validation`/`dataset_profile` run types existed in an earlier
version of the schema and were removed after confirming zero writers.

Episode has no equivalent quality-run cache: Episode readiness is always
computed live from the latest `EpisodeValidationRunRecord`/
`EpisodeProfileRunRecord` per episode (see
[Episode domain](./episode-domain.md) §5) — there is currently no
Episode-domain analogue to `DatasetVersion.validation_status`. `episode_count`
itself, however, *is* a real cached rollup (see below), independent of that
quality-run caching question.

#### 2.0.1 The aggregate-summary contract

`SceneVersionSummary.scene_count` and `EpisodeVersionSummary.episode_count`
(`packages/sceneops-core/sceneops_core/datasets/schemas/summaries.py`) are
**cached aggregate projections of that DatasetVersion's current canonical
membership** — `scene_count` must always equal a live count of `SceneRecord`
rows owned by `(dataset_id, dataset_version)`, and `episode_count` a live
count of `EpisodeRecord` rows, at the moment each was last written. They are
**not** a metric of the latest ingest/build/register operation
(`registered_episode_count` on `RegisterEpisodeJobResult`, or the number of
scenes a single `build_scenes`/`ingest_scenes` dispatch happened to process,
are the correct place for that — job results, never the DatasetVersion
summary).

Concretely, every writer recomputes from a live repository query
(`EpisodeRepository.count(...)`, or `SceneRepository.list(...)` in
`build_dataset_manifest`'s case) rather than incrementing a delta onto the
previous cached value — this is what keeps the count correct under retry,
upsert, duplicate input, and independent per-scene dispatches (as
`scripts/canonical/canonical_bootstrap.sh` performs one `register_episode`
dispatch per source scene): each dispatch converges on the true total, not
just what that one dispatch touched. `RegisterEpisodeJobHandler` is the sole
production writer of `episode_count` (it runs after `BuildEpisodesJobHandler`,
which only produces manifests — never an `EpisodeRecord` — so writing the
summary any earlier would count something that doesn't canonically exist
yet); `build_dataset_manifest`'s job handler is `scene_count`'s final writer
in both Scene pipelines, always re-querying every registered scene rather
than trusting its own pipeline batch's input.

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

This only serializes Episode-vs-Episode aggregate mutation for the *same*
row; it does not touch Scene's writers. That is safe, not incomplete,
because Scene and Episode summaries occupy disjoint columns
(`update_scene_summary`/`update_episode_summary` are both partial updates —
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
point. **Residual limitation:** this guarantees correctness of *committed*
state; it says nothing about a caller observing a value mid-flight between
two overlapping transactions (ordinary READ COMMITTED behavior, not a new
gap this introduces). Scene's own aggregate writers remain unlocked
(current production usage never dispatches Scene ingestion more than once
concurrently for the same DatasetVersion — see
[Canonical baseline doc](../development/canonical-baseline.md) — so this is
a documented, not a hidden, gap); the same `lock_for_update` primitive is
reusable there without new architecture if that assumption ever changes.

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

`scenes` — the canonical Scene-domain unit.

Key fields:
- `origin_type`, `generation_method`: where a scene came from (raw
  ingestion vs. reconstruction vs. simulation, etc.).
- `parent_scene_id`, `lineage` (JSONB): scene lineage — parent tracking for
  reconstructed/derived scenes.
- `scene_manifest_uri`, `world_state_manifest_uri`, `artifact_root_uri`:
  ArtifactStore references.
- `has_ground_truth` / `ground_truth_source`: GT presence and origin.
- `sample_count` / `frame_count` / `annotation_count` / `channels`: scene
  statistics.
- `status`: `SceneStatus` — see [Scene domain](./scene-domain.md) §4.

`scene_run_records` — unified scene-scope run table
(`scene_validation` / `scene_profile`).

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

`robot_runs` columns: `run_id`, `robot_id`, `started_at`, `ended_at`,
`recording_format`, `source_clock`, `recording_artifact_id`,
`manifest_artifact_id` (both FK `artifacts`, `ON DELETE RESTRICT`),
`manifest_checksum`, `registered_at`. A RobotRunRecord has no status, no
dataset membership and no recording URI: the recording URI/checksum/size
live on its recording ArtifactRecord, and channel/capture facts live only in
the manifest. It is never updated. Re-registering the same manifest is a
no-op; a different manifest for an existing `run_id` fails. A manifest
`robot_platform` fills an empty `Robot.platform` once and fails registration
if it contradicts a set one.

`Mission`/`RobotState` are populated by the `ingest_robot_states` Job, which
reads a rosbag2/MCAP file through `RosbagAdapter`. See
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
`dataset_scene_ingestion`, `raw_log_scene_building`, `scene_registration`,
`scenario_curation`, `detection_evaluation`, `raw_log_episode_building`.

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
INGEST_SCENES, BUILD_SCENES                          # source -> scene
BUILD_DATASET_MANIFEST, BUILD_SCENE_INDEX             # dataset-level aggregation
VALIDATE_SCENE, PROFILE_SCENE, REGISTER_SCENE          # scene-level
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
