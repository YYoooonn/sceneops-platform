# SceneOps Data Model

> Based on `packages/sceneops-db/sceneops_db/models/*.py` and
> `packages/sceneops-core/sceneops_core/*/schemas/`. Lists entity roles and
> the relationships/status values that matter; not every column — read the
> model files directly for full field lists.

## 1. Entity overview

```text
Dataset
 +- DatasetVersion
     +- DatasetVersionMember (pins one Scene or Episode revision per unit)

SceneRecord (scenes: the unit)
 +- SceneRevisionRecord (scene_revisions: one per registered manifest)
     +- SceneRunRecord (validation / profile, pins the revision)

EpisodeRecord (episodes: the unit)
 +- EpisodeRevisionRecord (episode_revisions: one per registered manifest)
     +- EpisodeRunRecord (validation / profile, pins the revision)

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
artifact, not a table: each `scene_revisions` row pins one by
`manifest_artifact_id` and projects it (see [Scene domain](./scene-domain.md)).
`EpisodeManifest` (with its `EpisodeLineage`) is likewise an immutable
artifact pinned by an `episode_revisions` row (see
[Episode domain](./episode-domain.md)). Units, revisions and members are three
separate concepts: a unit is identified by its source, a revision by its manifest
checksum, and a DatasetVersion holds units only through member pins (§3). Prediction runs are `InferenceRunModel` in code (table `inference_runs`).

## 2. Dataset / DatasetVersion

`datasets` — a logical dataset (e.g. `nuscenes`). `dataset_id` is the PK,
plus a `default_version` pointer. A Dataset is a namespace with mutable display
metadata, not versioned content.

`dataset_versions` — one committed, immutable composition per version
(ADR-015 §7, MS-8). `(dataset_id, version)` is unique and is bound once to one
composition.

Key fields:
- `composition_checksum`: the identity of the member pins (§2.1).
- `selector`: the `RevisionSelector` the members were resolved from, or NULL
  when the composing Job was given explicit pins (§2.1).
- `scene_count` / `keyframe_count` / `observation_count` / `observed_channels`:
  the Scene summary (`SceneVersionSummary`).
- `episode_count`: the Episode summary (`EpisodeVersionSummary`).
- `created_at`.

A DatasetVersion has no status and no update time: if its row exists, it is
complete. It holds no channel requirements and no free-form metadata; channel
requirements are pipeline / job parameters (ADR-007 §16, I-59). It carries no
source location or source format and relates to RobotRuns only through its
units' provenance. `datasets` has no `type`.

A DatasetVersion caches no quality result. Scene and Episode readiness are
derived from run records (`scene_run_records` / `episode_run_records`),
only from runs of the revision each member pins (see
[Quality and run records](./quality-and-runs.md)).

`dataset_version_members` — the version's units, each pinned at exactly one
registered revision (§3).

Schema rules: the summary columns have no default, are non-negative and count at
least one member (`ck_dataset_versions_counts`); `composition_checksum` is a
sha256 (`ck_dataset_versions_composition_checksum`); a `BEFORE UPDATE` trigger
(`sceneops_reject_update`) rejects any UPDATE; a Dataset with versions and a
version with members cannot be deleted (RESTRICT).

PostgreSQL also enforces the membership itself, for any SQL, not only for the
repository's single write:

```text
complete     dataset_versions_members_complete: a deferred constraint trigger checks
             every inserted version row at commit; its scene / episode member rows
             must number exactly scene_count / episode_count, so a positive count
             without members cannot commit
closed       dataset_version_members_closed: a member is inserted only in the
             transaction that inserted its version row (decided from the version
             row's xmin, savepoints included); a member of a committed version is
             refused
undeletable  dataset_version_members_undeletable: a member row is never deleted;
             UPDATE is refused by sceneops_reject_update; with members RESTRICTing
             their version, a committed version cannot be deleted either
```

### 2.1 Composition

`COMPOSE_DATASET_VERSION` is the only writer of `dataset_versions` and
`dataset_version_members` (`sceneops_worker.datasets.composition`). It is a Job,
submitted through `POST /api/v1/jobs`; no dataset route writes a version.

```text
params    dataset_id (an existing Dataset), dataset_version (the new name),
          exactly one of
            pins       the complete list of RevisionPins, at most one per unit
            selector   RevisionSelector {sources: [RevisionSourceClause]}
stage     reads only: resolve the input to registered revisions once; every
          pin must match a revision row exactly; a selector clause must select
          at least one revision; at most one revision per unit; at least one
          member; composition_checksum and summaries
converge  name exists + same composition_checksum      -> no-op (created = false)
          name exists + other composition_checksum     -> fail
commit    one transaction: the version row, then every member row
result    dataset_id, dataset_version, composition_checksum, created,
          member_count, scene_count, episode_count
```

```text
composition_checksum = "sha256:" + sha256(canonical_json({
    "composition_schema": "sceneops.dataset_version_composition/v1",
    "members":            sorted [[domain, unit_id, manifest_checksum], ...]
}))
```

The checksum covers the set of pins only: not their order, the selector, the
version name or anything mutable. `manifest_artifact_id` is a function of
`(unit_id, manifest_checksum)`, so it adds nothing. Two versions may have equal
compositions under different names.

`RevisionSourceClause {domain, robot_run_id, producer_fingerprint}` selects every
revision of a `domain` unit sourced from `robot_run_id` and registered under
`producer_fingerprint`. One unit and one fingerprint have at most one revision
(MS-7), so a clause selects at most one revision per unit; clauses are unique and
kept in canonical order, and their union is the selection. A selector is resolved
once, by the composing Job, and stored beside the members it resolved to. It is
never re-resolved, so registering more revisions later never changes a version.

Summaries are computed once, in the composing Job, from the projections of the
member revisions (`summarize_composition`). A domain with no member has no
summary (`scene` / `episode` is null).

Failure and concurrency:

- **Rows only.** Composition writes no object and reads none. It relies on revision
  registration's durability gate (§3), not on the members' foreign keys, for the
  bytes a member references.
- **No partial version.** Staging writes nothing; the commit is one transaction, so
  an invalid pin (even the last one), a crash or a failed commit leaves no version row
  and no member.
- **Races.** The version row is inserted first. A concurrent composition of the same
  name waits on its key, fails when the first commits, and re-runs the convergence
  check against the committed version. No row lock is taken: nothing writes a version
  after commit.
- **Job retries.** Job state and the composition are separate commits. A Job whose
  composition committed but whose success was never recorded is reclaimed
  (ADR-010) and re-run, and the re-run converges as a no-op. That convergence, not a
  fence, makes duplicate execution safe. Params keep pins in canonical order, so
  reordered pins share one execution key.

## 3. Units, revisions and DatasetVersion membership

Contract: [Scene domain](./scene-domain.md) §2,
[Episode domain](./episode-domain.md) §5,
[Ingress boundaries](./ingress-boundaries.md) §6.

```text
scenes / episodes                      the unit
  scene_id | episode_id   PK           unit_id v2 = f(domain, source_kind,
                                       robot_run_id, unit_key); no DatasetVersion
  source_kind                          'recording' (CHECK)
  robot_run_id            FK robot_runs RESTRICT
  unit_key                             unique (robot_run_id, unit_key)
  registered_at

scene_revisions / episode_revisions    one registered manifest of a unit
  (unit id, manifest_checksum)  PK     FK unit RESTRICT
  manifest_artifact_id    FK artifacts RESTRICT, unique; = f(unit id, checksum)
  producer_fingerprint                 unique (unit id, producer_fingerprint)
  window_clock, window_[start|end]_timestamp_ns   CHECK end > start
  projection columns                   Scene: observed_channels, counts;
                                       Episode: per-role topics, counts
  unique (unit id, manifest_artifact_id, manifest_checksum)   the full pin

dataset_version_members                one pin per unit per DatasetVersion
  (dataset_version_id, unit_id)  PK    FK dataset_versions RESTRICT
  domain                               'scene' | 'episode'
  scene_id | episode_id                exactly one, = unit_id (CHECK)
  manifest_artifact_id, manifest_checksum
  FK (scene_id | episode_id, manifest_artifact_id, manifest_checksum)
       -> that domain's revision pin, RESTRICT
```

Rules the schema enforces:

- **Identity.** One source unit has one unit row (`uq_*_source_unit`); a writer
  that derived another id for it fails instead of forking identity. The
  manifest ArtifactRecord id is the revision's deterministic id, so one
  artifact belongs to exactly one revision.
- **Source binding.** Unit rows admit recording sources only (`ck_*_source_kind`,
  `robot_run_id` NOT NULL). The source-unit key is per source column, so it never
  relies on a nullable column: a source kind with its own column (a content id)
  needs its own unique key and a CHECK that exactly the column of the row's
  `source_kind` is set. One unique key over nullable source columns would not hold
  (PostgreSQL treats NULLs as distinct), and with `NULLS NOT DISTINCT` it would make
  equal unit keys of different sources conflict. The registration code compares every
  unit column with the candidate, not a fixed column list.
- **Revisions.** A unit has one or more revisions and no "current" one. One unit
  and one producer fingerprint have at most one manifest checksum
  (`uq_*_revisions_fingerprint`, MS-7): a second checksum is a
  non-deterministic producer.
- **Immutability.** Units, revisions and members are never updated: a
  `BEFORE UPDATE` trigger (`sceneops_reject_update`) rejects any UPDATE.
- **Pins.** A member, and a per-unit run record, reference a revision by its
  full pin, so a pin whose artifact id and checksum disagree, or that names no
  registered revision, is refused. These composite foreign keys are `MATCH
  SIMPLE`, which skips the check when any column is NULL, so a CHECK makes the
  referencing columns all set (a member's domain column, a per-unit run record's
  pin) or, for a run record's job-level row, all NULL. A pinned revision, a unit with revisions, a
  manifest artifact with a revision, and a DatasetVersion with members cannot
  be deleted (RESTRICT).
- **Membership.** A DatasetVersion pins at most one revision per unit; one
  revision may be pinned by many DatasetVersions.

Registration writes units and revisions only by insert-if-absent and decides
each candidate with `decide_revision_registration` against the committed
revisions of its unit (`sceneops_db.repositories.revisions`). It writes a
revision row only after the durability and integrity gate
([Scene domain](./scene-domain.md) §3), which a foreign key cannot prove.
Concurrent registrations of one unit are decided by the unique keys, not by a
lock: identical registrations converge on one row, and of two checksums under
one fingerprint exactly one commits. A registration inserts its units in unit id
order, so two overlapping registrations wait for each other instead of
deadlocking.

`scene_run_records` / `episode_run_records` — unified run tables
(`scene_validation` / `scene_profile`, `episode_validation` /
`episode_profile`). A per-unit row pins the revision it assessed
(`fk_*_run_records_revision` to the full pin); a job-level aggregate row has
neither a unit nor a pin (CHECK `ck_*_run_records_revision_pin`). Run records
name no DatasetVersion: a revision is assessed once, however many versions pin
it.

## 4. Episode projection

An Episode revision projects, besides the shared pin and window columns, the
topics of each role with at least one occurrence
(`observation_topics` / `state_topics` / `action_topics` / `event_topics`) and
the matching counts. Episodes carry no status, task or outcome column (see
[Episode domain](./episode-domain.md) §5).

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
  -> Recording Publisher (sceneops_recording, no DB)
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
(see [Current limitations](./limitations.md)).

## 7. PipelineRun / PipelineTaskRun

`pipeline_runs` — one pipeline execution. `type` is `PipelineType`:
`recording_scene_building`, `recording_episode_building`,
`scene_ml_evaluation`, `episode_learning_data_building`.

`pipeline_task_runs` — individual tasks inside a run. `task_order` gives
sequence; `depends_on_task_ids` (JSONB) names the upstream tasks whose outputs
feed a task, but `PipelineOrchestrator` does not schedule from that graph —
tasks run strictly one at a time in `task_order` (see
[Jobs and pipelines](./jobs-and-pipelines.md) §4). `job_type`/`job_id` point at
the durable Job that does the task's work; the task run never executes it.

`PipelineRunStatus`: `pending -> queued -> running -> (blocked | succeeded
| failed | cancelled)`. `blocked` is what a quality gate produces when it
stops a pipeline mid-run. A run's `updated_at` is also when an `advance` was last
requested for it: execution recovery re-sends `advance` for a run waiting on one
longer than its threshold and moves `updated_at` to that resend.

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

`retry_count`/`max_retries` are enforced by `JobService.mark_queued` when a
`FAILED` Job is explicitly redispatched, which fails with a `ValueError` past the
cap. `lease_generation` numbers the claims of a Job and fences every write of the
current one; `lease_expires_at` is the time (PostgreSQL's clock) until which that
claim holds the Job, renewed with `heartbeat_at` while the worker runs and checked by
job lease recovery. `worker_id` (`celery:<task id>`) and `locked_at` record who
claimed it and when; a redelivered message repeats its `worker_id`, so it never
decides ownership. `queued_at` is the Job's last dispatch: every dispatch, lease
reclaim and resend of a lost message sets it, and execution recovery re-sends the
message of a `QUEUED` Job whose `queued_at` is older than its threshold (see
[Jobs and pipelines](./jobs-and-pipelines.md) §5, §8). `enqueued_at` is when the Job
entered `QUEUED` from another status (dispatch, retry, lease reclaim); a redispatch or
resend keeps it, so it is the start of the current wait that queue-time metrics are
measured from (§5 "Execution health").

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
`checksum` are nullable columns. Recording registration, observation payloads,
analytics tables and every derived artifact populate them, and a derived
record's id is derived from its checksum — see
[Storage layout](./storage-layout.md).

See [Artifact ownership and publication](./jobs-and-pipelines.md#7-artifact-ownership-and-publication)
for the "producer owns the ArtifactRecord" invariant and how Scene/Episode
apply it.

## 11. ExecutionRecord

`execution_records` — the audit record of a message sent to Celery: a Job to run or
a PipelineRun to advance. `execution_backend` (`celery`), `execution_kind` (`job_run` /
`pipeline_run`), `resource_id` (a `job_id` or `pipeline_run_id`) and `external_id` (the
Celery task id) separate the logical resource from the physical dispatch. It is written
once, after the message is sent, by the API dispatch facades and by the orchestrator
when it dispatches a task's Job, and is never updated: it records the send, not what
became of the work. The state of the work is the Job's and the PipelineRun's own. See
[Jobs and pipelines](./jobs-and-pipelines.md) §5 for the window this leaves.
