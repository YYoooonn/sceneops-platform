# Jobs and Pipelines

> Based on `packages/sceneops-core/sceneops_core/pipelines/builtin.py`,
> `apps/worker/sceneops_worker/pipelines/`, and
> `apps/worker/sceneops_worker/jobs/`. Decision record: [ADR-007](../adr/007-canonical-ingestion-architecture.md) §34.

## 1. Two execution units: Pipeline and Job

```text
PipelineRun
 +- PipelineTaskRun (order, depends_on — declared but unused, see §3)
     +- delegates to -> Job
             +- actual handler execution (per JobType)
```

A Pipeline strings several Jobs together in a fixed order; a Job is the
smallest unit that actually does work. The API can dispatch a Job standalone
or as part of a Pipeline.

## 2. The four Pipelines

A Pipeline exists only where multi-stage orchestration, retry and lineage
justify it. SceneOps has exactly four; every other operation is a Job (§2.1).

```text
RECORDING_SCENE_BUILDING          RobotRun -> Scenes
  build_recording_scenes -> register_scenes -> validate_scene -> profile_scene (optional)

RECORDING_EPISODE_BUILDING        RobotRun -> Episodes
  build_recording_episodes -> register_episodes -> validate_episode -> profile_episode (optional)

SCENE_ML_EVALUATION               pinned label sets + policy + configs -> views -> ScenarioSet -> prediction -> evaluation
  build_scene_sample_views -> mine_scenarios -> score_scenario_readiness
                           -> predict_detection -> evaluate_detection

EPISODE_LEARNING_DATA_BUILDING    pinned Episodes + alignment config -> AlignedEpisodes -> LearningDataExport
  align_episode -> export_learning_data
```

**Canonical building (L1 -> L2).** `RECORDING_SCENE_BUILDING` builds the
canonical Scenes of one registered RobotRun (`build_recording_scenes` params:
`robot_run_id`, `build_config`) and hands the complete set to
`register_scenes` through the `manifest_artifact_ids` REF; `register_scenes`
hands `scene_ids` to the quality stages. `RECORDING_EPISODE_BUILDING` is its
Episode sibling: `build_recording_episodes` hands the complete Episode set to
`register_episodes` (`manifest_artifact_ids`), which hands `episode_ids` to
`validate_episode` / `profile_episode`. One run covers one RobotRun; a
DatasetVersion spanning several RobotRuns takes several runs. The two never
depend on each other and share the RobotRun's `OBSERVATION_PAYLOAD`
artifacts. See [Scene domain](./scene-domain.md) and
[Episode domain](./episode-domain.md).

**Scene ML (L3).** `SCENE_ML_EVALUATION` consumes registered Scenes, an
explicit sample-view policy and pinned label set revisions. Each stage pins the
revisions it consumes and hands the next stage its own output through a REF:
`build_scene_sample_views` -> `views` -> `mine_scenarios` (curates the
ScenarioSet revision) -> `scenario_set_id` -> `score_scenario_readiness` and
`predict_detection` -> `inference_run_id` + `prediction_manifest_checksum` ->
`evaluate_detection` (scores that prediction revision against the pinned label
set revision). A caller that pins sample views itself is never overridden by a
REF. Policy, channels, label sets, categories and the model backend are
explicit params; no stage defaults a source's vocabulary. See
[Derived layer](./derived-layer.md).

**Episode learning (L3).** `EPISODE_LEARNING_DATA_BUILDING` aligns pinned
Episode revisions (`align_episode` params: `episodes` — each an `episode_id`,
optionally pinned by `source_artifact_id` + `source_manifest_sha256` — and one
`alignment_config`) and hands the aligned revisions to `export_learning_data`
in the export's own input shape (`export_inputs`: episode id, aligned artifact
id, checksum). The export validates every aligned input and fails rather than
publishing over an invalid one. The canonical Episodes are never rewritten.

### 2.1 Atomic Jobs

Reusable single operations dispatch as plain Jobs (`POST /jobs`), not through a
Pipeline:

```text
REGISTER_ROBOT_RUN        POST /robot-runs:register (create + dispatch)
IMPORT_LABELS             a label document -> an independent, pinned LabelSet revision
BUILD_SCENE_SAMPLE_VIEWS  Scenes + policy + label sets -> pinned views (also the first stage of SCENE_ML_EVALUATION)
MINE_SCENARIOS /          ScenarioSet curation and readiness scoring over pinned views
  SCORE_SCENARIO_READINESS   (stages of SCENE_ML_EVALUATION)
PREDICT_DETECTION         pinned views or a ScenarioSet + model -> prediction revision
EVALUATE_DETECTION        a prediction revision + a pinned label set -> evaluation
ALIGN_EPISODE             pinned Episodes + config -> AlignedEpisodes (first stage of EPISODE_LEARNING_DATA_BUILDING)
VALIDATE_ALIGNED_EPISODE /
  PROFILE_ALIGNED_EPISODE structural validation / profile of one pinned aligned revision
EXPORT_LEARNING_DATA      pinned aligned revisions -> a sharded learning export
CURATE_EPISODES           a selection policy over one learning export (read by SceneOpsDataset)
EXPORT_ANALYTICS_SNAPSHOT Scene analytical Parquet tables of a DatasetVersion
INGEST_ROBOT_STATES /     robot telemetry projection of a RobotRun
  EXPORT_ROBOT_ANALYTICS_SNAPSHOT
VALIDATE_* / PROFILE_*    the quality jobs the building pipelines also run as stages
BUILD_RECORDING_* / REGISTER_*   the build and registration stages of the two building pipelines
```

The pipeline-stage jobs and the atomic jobs are the same handlers: a job that is
a stage of a Pipeline is also dispatchable alone, so old and new flows share
one correctness path.

## 3. How data moves between tasks: output kind

Each task's outputs are classified by `PipelineTaskOutputKind`
(`builtin.py`):

- `REF` — a reference the next task consumes as input (e.g.
  `episode_ids`, `validation_run_id`) — the real
  connective tissue between tasks.
- `SUMMARY` — aggregate statistics, written straight into a DB column
  (e.g. `scene_count`, `issue_count`).
- `METRIC` — evaluation-style metrics.
- `ARTIFACT` — an output URI nothing downstream consumes (reports, log-like
  files).

This split makes explicit, at definition time, which outputs are "data
flowing through the pipeline" and which are "just recorded byproducts."

`depends_on_task_ids` (JSONB on `pipeline_task_runs`) is populated but not
used for scheduling — `PipelineRunner` always executes tasks in
`task_order`, never by resolving this graph. It exists for future use, not
as dead data (the field is written and could be read by a smarter scheduler
without a schema change).

## 4. Quality gate

`PipelineTaskQualityRule` can block a pipeline even after a task succeeds.
Example:

```python
PipelineTaskQualityRule(
    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
    source="summary.should_block_pipeline",
    message="Episode validation blocked pipeline",
    code="validate_episode_blocked",
)
```

If a validation task returns `should_block_pipeline=true`, the task itself
is still `SUCCEEDED`, but the pipeline ends `PipelineRunStatus.BLOCKED`
(`validate_episode_blocked` for `validate_episode`; Scene validation uses
the same rule shape once a Scene pipeline includes it). There is no separate quarantine state —
blocking is expressed purely as "the pipeline stops here," at the pipeline
level.

## 5. Execution flow

### PipelineRunner (`sceneops_worker/pipelines/runner.py`)

1. Load the run by `pipeline_run_id`, validate its current status —
   `SUCCEEDED`/`RUNNING`/`CANCELLED` reject re-run; `BLOCKED`/`FAILED`
   allow it (a blocked-by-quality-gate or failed pipeline should be
   re-runnable once the underlying cause is fixed — the API-level
   `PipelineService.validate_executable` matches this rule; an earlier
   version had the worker reject `BLOCKED` re-runs while the API allowed
   them, which was a real bug, now fixed).
2. Transition to `RUNNING`.
3. Run `PipelineDefinition.tasks` sorted by `order`, **sequentially**
   (`depends_on_task_ids` is not used for scheduling, see §3). A task
   that's already `SUCCEEDED` is skipped, not re-run
   (`PipelineTaskRunner._handle_pre_execution_state`) — so a redispatch is
   already a **partial retry** by default, resuming from the first
   failed/blocked task.
4. A blocked task ends the pipeline `BLOCKED` immediately.
5. An exception ends the pipeline `FAILED` (recording whichever tasks did
   complete) and re-raises, so Celery's `autoretry_for` can retry the whole
   pipeline from the top.

### JobRunner (`sceneops_worker/jobs/runner.py`)

1. `claim_for_run` claims a job only from `PENDING`/`QUEUED` (prevents
   concurrent execution).
2. Transition to `RUNNING` -> run the handler -> record the result.
3. On exception: roll back the context, record the failure, re-raise.

## 6. Job steps: `JOB_STEP_DEFINITIONS_BY_TYPE`

Every `JobType` has a registered handler and a
declarative list of named steps in
`packages/sceneops-core/sceneops_core/jobs/schemas/step_registry.py`, e.g.:

```python
JobType.VALIDATE_SCENE: [
    step("resolve_scene_revisions", "Resolve scene revisions"),
    step("validate_scene_observations", "Validate scene observations"),
    step("validate_required_channels", "Validate required channels", optional=True),
    step("save_validation_report", "Save validation report"),
],
```

`create_initial_job_steps(job_type)` is called when a `Job` is created
(`apps/api/app/platform/jobs/service.py`) and when a pipeline plans a task
(`apps/worker/sceneops_worker/pipelines/planning.py`), so every `Job`
persists a `steps` list matching its type from the moment it's created.

**What actually updates at runtime is much narrower than the step list
suggests.** `JobResultRecorder` (`apps/worker/sceneops_worker/jobs/result_recorder.py`)
only ever touches the *first* `PENDING` step (marks it `RUNNING` when the
job starts) and whichever step is currently `RUNNING` (marks it
`SUCCEEDED`/`FAILED` when the job finishes). No handler reports progress
through its own intermediate steps, so on a successful job, step 1 shows
`SUCCEEDED` and every other declared step stays `PENDING` forever — even
though the work they describe did happen. Treat `JOB_STEP_DEFINITIONS_BY_TYPE`
as **declarative, UI-facing documentation of a job's internal stages**, not
as a live progress tracker. Wiring per-step progress reporting into handlers
would be the natural follow-up if step-level progress display is ever
needed — out of scope here, and not something to build without a concrete
consumer of that data.

## 7. Artifact ownership invariant

**The stage that physically writes an artifact's bytes owns its
`ArtifactRecord`.** A task that only reads an artifact by URI (passed
through `PipelineTaskInputs.refs`, an upstream task's `REF` output) never
creates its own `ArtifactRecord` for that same object.

This is enforced consistently across the platform:

- Scene: the producer that publishes a canonical SceneManifest owns its
  `SCENE_MANIFEST` `ArtifactRecord`; `register_scenes` re-reads and verifies
  the bytes by that artifact id and never creates an `ArtifactRecord` (see
  [Scene domain](./scene-domain.md) §3). `build_recording_scenes` also owns
  the `OBSERVATION_PAYLOAD` ArtifactRecords of the payloads it extracts;
  its payload and manifest artifact ids are deterministic, so a retry
  reuses an existing identical record instead of inserting a second one.
- Episode: `build_recording_episodes` owns its `EPISODE_MANIFEST` and
  `OBSERVATION_PAYLOAD` ArtifactRecords with deterministic ids (payload ids
  are shared with Scene builds of the same RobotRun, so whichever runs first
  creates them); `register_episodes` re-reads and verifies, never creates.

`ArtifactRecord` creation is otherwise insert-only with no deduplication —
this invariant is what keeps the `artifacts` table from accumulating
duplicate rows for the same physical object, not a database constraint.

Ownership is recorded via `ArtifactOwnerType`/`owner_id`, or one of the
direct scoping columns (`dataset_id`, `scene_id`, `pipeline_run_id`, etc. —
see [Data model](./data-model.md) §10). Scene has a dedicated `scene_id`
column on `ArtifactModel`; Episode does not, and is only reachable via
`owner_type=episode`/`owner_id` — this is a naming/reachability asymmetry
between the two domains' artifact APIs, not a bug (see
[Episode domain](./episode-domain.md) §5).

## 8. Reliability: idempotency, retry, partial failure

| Requirement | Current implementation |
| --- | --- |
| Retry (task-level) | Celery `autoretry_for=(Exception,)` retries a whole pipeline/job, separately from the fact that `PipelineTaskRunner` skips already-`SUCCEEDED` tasks — so an explicit human redispatch resumes only from the failed task. Standalone Job retry is enforced by `JobService.mark_queued` checking `jobs.retry_count`/`max_retries`; redispatching a `FAILED` job past the cap raises `ValueError`. |
| Idempotency | `execution_key = sha256(kind, type, dataset_id, dataset_version, model_id, model_version, params)` (`sceneops_core.executions.compute_execution_key`). Creating a `Job`/`PipelineRun` with a key that already has a `PENDING`/`QUEUED`/`RUNNING`/`SUCCEEDED` record returns the existing one instead of creating a new one. `force: true` bypasses this and always creates fresh. |
| Partial failure | `PipelineTaskRunner._handle_pre_execution_state` skips already-`SUCCEEDED` tasks on redispatch — a redispatch is a partial retry by construction. `PipelineRunner._validate_runnable` allows re-running `BLOCKED` pipelines (see §5). |
| Backfill | Not implemented — SceneOps has no time-partitioned execution concept (no daily DAG runs); every `pipeline_run` is already scoped independently to `(dataset_id, dataset_version)`. In this domain, "backfill" is equivalent to "dispatch one more `pipeline_run` for that version," which needs no separate mechanism. |

Implementation: `packages/sceneops-core/sceneops_core/executions/key.py`,
`apps/api/app/platform/jobs/service.py` (`create_job`, `mark_queued`),
`apps/api/app/platform/pipelines/service.py` (`create_pipeline_run`),
`apps/worker/sceneops_worker/pipelines/runner.py` (`_validate_runnable`).
Background: [ADR-004](../adr/004-airflow-vs-celery.md).

These primitives are independent of whether Airflow is involved — Airflow's
own retry also depends on "skip what already succeeded," the same
idempotency judgment `execution_key`/partial-retry already implement, so
they carry over unchanged if the Airflow backend is extended to more
pipeline types.

## 9. Job dispatch race avoidance

`JobDispatchFacade` enforces "commit `QUEUED` to the DB, then send to
Celery" — see [Architecture overview](./overview.md) §2. Skipping that
order risks the API's delayed `QUEUED` commit overwriting a state the
worker already advanced to `RUNNING`. The same race applies to any future
Airflow-for-Jobs path.

## 10. Airflow path: per-task DAGs

When `pipeline_backend=airflow`, each pipeline type runs as its own DAG
(`airflow/dags/sceneops_pipelines.py`, DAG id `<pipeline_dag_prefix>_<type>`,
default `sceneops_<type>`) where each task is its own `DockerOperator` process,
rather than one `PipelineRunner.run()` loop. The API picks the DAG by the run's
pipeline type. See [Architecture overview](./overview.md) §3 for what runs
where, and how `start()`/`finalize()` recompose the same private
state-transition methods the Celery path uses inline.

```text
start -> <task 1> -> ... -> <task n> -> finalize  (trigger_rule=all_done)
```

The DAG file cannot import SceneOps, so it mirrors the task ids of the four
definitions statically; `apps/worker/tests/pipelines/test_airflow_dag_mirror.py`
fails when the mirror drifts. Tasks run serially in definition order, and
optional tasks skip themselves exactly as on the Celery path. The orchestrator
acceptance is `make test-infrastructure-airflow`.
