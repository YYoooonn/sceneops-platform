# Jobs and Pipelines

> Based on `packages/sceneops-core/sceneops_core/pipelines/builtin.py`,
> `apps/worker/sceneops_worker/pipelines/`, and
> `apps/worker/sceneops_worker/jobs/`.

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

## 2. Built-in pipeline definitions

```text
DATASET_SCENE_INGESTION
  ingest_scenes -> register_scene -> validate_scene -> profile_scene
  -> build_scene_index -> build_dataset_manifest

RAW_LOG_SCENE_BUILDING
  build_scenes -> register_scene -> validate_scene -> profile_scene
  -> build_scene_index -> build_dataset_manifest

SCENE_REGISTRATION
  register_scene -> validate_scene -> profile_scene

SCENARIO_CURATION
  mine_scenarios -> score_scenario_readiness

DETECTION_EVALUATION
  predict_detection -> evaluate_detection

RAW_LOG_EPISODE_BUILDING
  build_episodes -> register_episode -> validate_episode -> profile_episode
```

`validate_scene -> profile_scene` repeats identically across three
pipelines (ingestion, raw-log-building, registration) — it's designed as a
data-source-agnostic quality stage, reused regardless of where the scene
came from. `validate_episode`/`profile_episode` play the same role for
Episode. See [Scene domain](./scene-domain.md) and
[Episode domain](./episode-domain.md) for the domain-specific detail.

Two Job types are deliberately **not** wrapped in any pipeline —
`ingest_robot_states` and `export_robot_analytics_snapshot`. Robot/RobotRun
is a separate domain from Dataset/DatasetVersion, so these dispatch as
plain Jobs via `POST /jobs`, not through a named pipeline — see
[Robot data ingestion](../workflows/robot-run-and-mcap.md).

## 3. How data moves between tasks: output kind

Each task's outputs are classified by `PipelineTaskOutputKind`
(`builtin.py`):

- `REF` — a reference the next task consumes as input (e.g.
  `scene_manifest_uris`, `episode_ids`, `validation_run_id`) — the real
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
    message="Scene validation blocked pipeline",
    code="validate_scene_blocked",
)
```

If `validate_scene` returns `should_block_pipeline=true`, the task itself
is still `SUCCEEDED`, but the pipeline ends `PipelineRunStatus.BLOCKED`.
`validate_episode` has the identical mechanism
(`validate_episode_blocked`). There is no separate quarantine state —
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

Every `JobType` (except the five reserved/orphan ones with no registered
handler — see [Reserved architecture](./reserved-and-limitations.md)) has a
declarative list of named steps in
`packages/sceneops-core/sceneops_core/jobs/schemas/step_registry.py`, e.g.:

```python
JobType.VALIDATE_SCENE: [
    step("load_scene_manifest", "Load scene manifest"),
    step("validate_scene_structure", "Validate scene structure"),
    step("validate_required_channels", "Validate required channels", optional=True),
    step("validate_assets", "Validate assets", optional=True),
    step("validate_world_state", "Validate world state", optional=True),
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

- Scene: `ingest_scenes`/`build_scenes` write and own `SCENE_MANIFEST`;
  `register_scene` reads it to upsert `SceneRecord`, and does not
  duplicate the `ArtifactRecord` (see [Scene domain](./scene-domain.md)
  §3 — this used to be violated, producing two `ArtifactRecord` rows for
  one URI).
- Episode: `build_episodes` writes and owns `EPISODE_MANIFEST`;
  `register_episode` reads it, same pattern.

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

## 10. Airflow path: per-task DAG (proof of concept)

When `pipeline_backend=airflow`, `dataset_scene_ingestion` runs as a DAG
(`airflow/dags/sceneops_pipeline_run.py`) where each task is its own
`DockerOperator` process, rather than one `PipelineRunner.run()` loop. See
[Architecture overview](./overview.md) §3 for the full breakdown of what
runs where, and how `start()`/`finalize()` recompose the same
private state-transition methods the Celery path uses inline.

```text
start
  -> ingest_scenes -> register_scene -> validate_scene
  -> profile_scene -> build_scene_index -> build_dataset_manifest
  -> finalize  (trigger_rule=all_done)
```

Scope is `dataset_scene_ingestion` only — the DAG's task chain is
hardcoded. Sending other pipeline types (including
`raw_log_episode_building`) through Airflow needs a generalized DAG or one
per type; not built.
