# Jobs and Pipelines

> Based on `packages/sceneops-core/sceneops_core/pipelines/builtin.py`,
> `apps/worker/sceneops_worker/{pipelines,jobs,execution,tasks}/` and
> `apps/api/app/platform/`. Decision record:
> [ADR-009](../adr/009-job-centric-execution-and-durable-boundaries.md).

## 1. Two units, one execution path

```text
PipelineRun
 +- PipelineTaskRun (order, depends_on_task_ids)
     +- owns -> Job (durable; one per attempt of the task)
                 +- JobRunner -> domain handler (per JobType)
```

A **Job** is the smallest unit that does work, and the only thing that runs a
domain handler. A **Pipeline** is durable orchestration state over Jobs: it never
executes a handler or a JobRunner itself. Every execution, whether a standalone
Job or a pipeline task, follows one path:

```text
PipelineRun -> PipelineTaskRun -> durable Job -> asynchronous dispatch
            -> Celery worker (sceneops.jobs) -> JobRunner -> domain handler
```

A standalone Job joins the path at "durable Job". `JobRunner` is the sole runtime
entry point for a Job: both the Celery task and the worker CLI
(`sceneops-worker jobs run`) call it.

## 2. The four Pipelines

A Pipeline exists only where multi-stage orchestration, retry and lineage justify
it. SceneOps has exactly four; every other operation is a Job (§2.1).

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

**Canonical building (RobotRun -> Scene / Episode).** `RECORDING_SCENE_BUILDING`
builds the canonical Scenes of one registered RobotRun (`build_recording_scenes`
params: `robot_run_id`, `build_config`) and hands the complete set to
`register_scenes` through the `manifest_artifact_ids` REF; `register_scenes` hands
`scene_ids` to the quality stages. `RECORDING_EPISODE_BUILDING` is its Episode
sibling: `build_recording_episodes` hands the complete Episode set to
`register_episodes` (`manifest_artifact_ids`), which hands `episode_ids` to
`validate_episode` / `profile_episode`. One run covers one RobotRun; a DatasetVersion
spanning several RobotRuns takes several runs. The two pipelines never depend on
each other and share the RobotRun's `OBSERVATION_PAYLOAD` artifacts. See
[Scene domain](./scene-domain.md) and [Episode domain](./episode-domain.md).

**Scene ML.** `SCENE_ML_EVALUATION` consumes registered Scenes, an explicit
sample-view policy and pinned label set revisions. Each stage pins the revisions it
consumes and hands the next stage its own output through a REF:
`build_scene_sample_views` -> `views` -> `mine_scenarios` (curates the ScenarioSet
revision) -> `scenario_set_id` -> `score_scenario_readiness` and `predict_detection`
-> `inference_run_id` + `prediction_manifest_checksum` -> `evaluate_detection`
(scores that prediction revision against the pinned label set revision). A caller
that pins sample views itself is never overridden by a REF. Policy, channels, label
sets, categories and the model backend are explicit params; no stage defaults a
source's vocabulary. See [Derived layer](./derived-layer.md).

**Episode learning.** `EPISODE_LEARNING_DATA_BUILDING` aligns pinned Episode
revisions (`align_episode` params: `episodes`, each an `episode_id` optionally
pinned by `source_artifact_id` + `source_manifest_sha256`, and one
`alignment_config`) and hands the aligned revisions to `export_learning_data` in the
export's own input shape (`export_inputs`: episode id, aligned artifact id,
checksum). The export validates every aligned input and fails rather than
publishing over an invalid one. The canonical Episodes are never rewritten.

### 2.1 Standalone Jobs

Reusable single operations are plain Jobs (`POST /jobs`, then
`POST /jobs/{id}/execute`), not Pipelines:

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
BUILD_RECORDING_* / REGISTER_* / VALIDATE_* / PROFILE_*   the stages of the two building pipelines, also dispatchable alone
```

A job that is a pipeline stage and the standalone job of the same type are the same
handler, so both flows share one correctness path. Every `JobType` has a registered
handler (`JobHandlerRegistry`).

## 3. How data moves between tasks

Each task's outputs are classified by `PipelineTaskOutputKind` (`builtin.py`):

- `REF` — a reference the next task consumes as input (for example `episode_ids`,
  `validation_run_id`); the connective tissue between tasks.
- `SUMMARY` — aggregate statistics recorded on the task result (for example
  `scene_count`, `issue_count`).
- `METRIC` — evaluation-style metrics.
- `ARTIFACT` — an output URI nothing downstream consumes (reports).

`depends_on_pipeline_task_ids` on a task definition names the upstream tasks whose
`REF` outputs `PipelineInputResolver` merges into the task's inputs, and the
definition registry validates that every named dependency exists. It does not drive
scheduling: the orchestrator walks tasks strictly one at a time in `order` (§4).

## 4. Pipeline orchestration

`PipelineOrchestrator.advance(pipeline_run_id)`
(`apps/worker/sceneops_worker/pipelines/orchestrator.py`) takes one orchestration
step over durable state and returns. A step runs in one transaction under a row lock
on the PipelineRun (`get_for_update`), so concurrent or duplicated steps of one run
serialize and converge.

```text
QUEUED    start: RUNNING; every task that is not SUCCEEDED or SKIPPED goes back to PENDING
          (a FAILED or BLOCKED run therefore resumes at its first unfinished task)
RUNNING   walk the tasks in definition order:
            SUCCEEDED / SKIPPED       next task
            RUNNING, Job in flight    wait; nothing to do until the Job reports
            RUNNING, Job terminal     record the result, apply the quality gate
                                      -> task SUCCEEDED, or BLOCKED / FAILED and the run ends
            PENDING                   skip an optional task that has no params, else
                                      create its Job, commit, dispatch it, and return
          no task left: the run SUCCEEDED
other     nothing to do (not started, already terminal, or a late report)
```

A step is triggered by exactly two events:

1. `POST /pipelines/runs/{id}/execute` sends the run's first `advance` message
   (pipeline queue) after committing the run as `QUEUED`.
2. `JobRunner` sends an `advance` message after it has committed the terminal state
   of a Job that belongs to a PipelineRun.

Tasks run **strictly serially**: one Job is in flight per run. A task whose Job
cannot be created (its inputs do not resolve, its params are invalid) is created
inside a savepoint, so it leaves no Job behind and the run ends `FAILED`. A Job
created by a task carries `max_retries=0` and no `execution_key`; running a task
again means re-executing the run, which creates a new Job for each unfinished task.
Re-execution is allowed from `PENDING`, `QUEUED`, `FAILED` and `BLOCKED`, and is
refused for `RUNNING`, `SUCCEEDED` and `CANCELLED`.

### Quality gate

`PipelineTaskQualityRule` can block a pipeline even after its Job succeeded:

```python
PipelineTaskQualityRule(
    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
    source="summary.should_block_pipeline",
    message="Episode validation blocked pipeline",
    code="validate_episode_blocked",
)
```

If a validation task reports `should_block_pipeline=true`, its Job is `SUCCEEDED`,
the task run is `BLOCKED` and the pipeline ends `BLOCKED`. There is no separate
quarantine state: blocking means "the pipeline stops here". A blocked run can be
re-executed once the cause is fixed.

## 5. Job execution and reliability

### JobRunner (`sceneops_worker/jobs/runner.py`)

1. `claim_for_run` is one atomic `UPDATE ... WHERE status IN (PENDING, QUEUED)
   RETURNING`, so a duplicated or late Celery message cannot run a Job twice. A Job
   that cannot be claimed (missing, running, terminal) makes `run` raise.
2. The Job is committed as `RUNNING`, the handler runs, and the terminal state is
   committed. A handler failure is a persisted `FAILED` Job (with its error), not an
   exception: the returned Job is the outcome.
3. If the Job belongs to a PipelineRun, `JobRunner` then sends one `advance` message.

There is no Celery-level retry on either task. Retrying is always an explicit
redispatch of durable state.

### Idempotency and retry

| Concern | Current implementation |
| --- | --- |
| Request identity | `execution_key = sha256(kind, type, dataset_id, dataset_version, model_id, model_version, params)` (`sceneops_core.executions.compute_execution_key`). Creating a Job or PipelineRun whose key already has a `PENDING` / `QUEUED` / `RUNNING` / `SUCCEEDED` record returns that record; `force: true` always creates a new one. Some job types normalize their params first so equivalent requests share a key (`params_for_execution_key`). |
| Standalone Job retry | Redispatching a `FAILED` Job (`POST /jobs/{id}/execute`) increments `retry_count`; past `max_retries` it is refused. |
| Pipeline retry | Re-executing a `FAILED` or `BLOCKED` run resumes at its first unfinished task; succeeded and skipped tasks are not re-run. |
| Duplicate delivery | The atomic Job claim and the row-locked, idempotent orchestration step make a duplicated message harmless. |
| Duplicate output | Derived artifacts are write-once and content-pinned, so a Job that re-runs converges on the same objects and records (§7). |
| Backfill | None: there is no time-partitioned execution; one more PipelineRun is dispatched for the scope. |

Implementation: `packages/sceneops-core/sceneops_core/executions/key.py`,
`apps/api/app/platform/jobs/service.py` (`create_job`, `mark_queued`),
`apps/api/app/platform/pipelines/service.py` (`create_pipeline_run`, `validate_executable`).

### Durable-state-first boundaries and failure windows

Every dispatch commits durable state first and sends the message second, so a
message never refers to a row that is not committed. The remaining windows are
consequences of that order:

```text
API dispatch     mark QUEUED + commit | send_task | commit ExecutionRecord
                   send fails          record stays QUEUED, nothing in flight; dispatch again
                   record commit fails message is in flight with no ExecutionRecord

Orchestrator     create Job + task RUNNING, commit | send_task | commit ExecutionRecord
                   send fails          the Job stays QUEUED and the run waits on it; nothing
                                       re-sends it automatically

JobRunner        claim + RUNNING | handler | terminal state commit | send advance
                   worker dies in the handler   the Job stays RUNNING (§8)
                   advance message lost         the run stays RUNNING with a terminal Job;
                                                one `advance` step observes it
```

`make worker-advance-pipeline PIPELINE_RUN_ID=…` takes that single `advance` step by
hand, and a `QUEUED` Job can be dispatched through `POST /jobs/{id}/execute`.

## 6. Job steps: `JOB_STEP_DEFINITIONS_BY_TYPE`

Every `JobType` has a registered handler and a declarative list of named steps in
`packages/sceneops-core/sceneops_core/jobs/schemas/step_registry.py`, for example:

```python
JobType.VALIDATE_SCENE: [
    step("resolve_scene_revisions", "Resolve scene revisions"),
    step("validate_scene_observations", "Validate scene observations"),
    step("validate_required_channels", "Validate required channels", optional=True),
    step("save_validation_report", "Save validation report"),
],
```

`create_initial_job_steps(job_type)` runs when a Job is created
(`apps/api/app/platform/jobs/service.py`) and when the orchestrator plans a task
(`apps/worker/sceneops_worker/pipelines/planning.py`), so every Job persists a
`steps` list matching its type.

Only the first step is updated at runtime. `JobResultRecorder`
(`apps/worker/sceneops_worker/jobs/result_recorder.py`) marks the first `PENDING`
step `RUNNING` when the Job starts and the running step `SUCCEEDED` / `FAILED` when it
finishes; no handler reports progress through its own steps, so on a succeeded Job the
other declared steps stay `PENDING`. Treat the step list as declarative documentation
of a job's stages, not as a progress tracker.

## 7. Artifact ownership and publication

**The stage that physically writes an artifact's bytes owns its `ArtifactRecord`.**
A task that only reads an artifact by URI (passed through `PipelineTaskInputs.refs`)
never creates its own `ArtifactRecord` for that object:

- Scene: the producer that publishes a canonical SceneManifest owns its
  `SCENE_MANIFEST` record; `register_scenes` re-reads and verifies the bytes by
  artifact id and never creates a record ([Scene domain](./scene-domain.md) §3).
  `build_recording_scenes` also owns the `OBSERVATION_PAYLOAD` records of the
  payloads it extracts.
- Episode: `build_recording_episodes` owns its `EPISODE_MANIFEST` and
  `OBSERVATION_PAYLOAD` records; payload ids are shared with Scene builds of the same
  RobotRun, so whichever runs first creates them. `register_episodes` re-reads and
  verifies, never creates.

Publication is content-pinned and convergent
(`apps/worker/sceneops_worker/derived/publication.py`, `ArtifactRecordStore.register`):
bytes are written once at a checksum-qualified key, the record id derives from the
content, a retry or concurrent producer of identical content converges on the same
object and record, and a record that disagrees with an existing one on kind,
location, checksum or size fails loudly (`ArtifactRecordConflictError`). Changed
content for the same logical artifact is a new revision beside the old one. See
[Storage layout](./storage-layout.md).

Ownership is recorded by `ArtifactOwnerType` / `owner_id` or one of the direct scoping
columns (`dataset_id`, `scene_id`, `pipeline_run_id`, ...); see
[Data model](./data-model.md). Scene has a dedicated `scene_id` column on the
artifact table; Episode does not and is reachable through `owner_type=episode` /
`owner_id` ([Episode domain](./episode-domain.md) §5).

## 8. Current limitations of execution

These are the verified gaps of the current design; none has a mechanism today.

- **No stall or worker-loss recovery for Jobs and Pipelines.** `claim_for_run`
  sets `locked_at` / `heartbeat_at` once; nothing refreshes or inspects them. With
  `task_acks_late` and `task_reject_on_worker_lost`, Celery redelivers the message of a
  lost worker, but the Job is `RUNNING` and cannot be claimed again, so it stays
  `RUNNING` and its run stays `RUNNING`. Only `REGISTER_ROBOT_RUN` has stall handling,
  in the acquisition reconciler
  ([Robot data ingestion](../workflows/robot-run-and-mcap.md) §3.2).
- **Lost or unsent messages are not re-sent.** An `advance` message that is lost, or a
  Job dispatch that fails after its commit (§5), leaves durable state that waits for
  an event that does not come.
- **Dispatch and database are not one atomic unit.** A message can be sent without its
  `ExecutionRecord` being committed, and a record can be committed without a message.
- **Blocking broker sends in async code.** `Celery.send_task` is synchronous; the API's
  `dispatch_job` / `dispatch_pipeline` and the worker's `ExecutionDispatcher` call it
  from `async` code, so the event loop waits for the broker round trip.
- **Strictly serial pipeline scheduling.** One task's Job is in flight per run;
  `depends_on_pipeline_task_ids` is not a scheduling graph.
- **No cancellation.** `CANCELLED` statuses exist, and re-execution and `JobRunner`
  refuse a cancelled record, but no API or worker path sets one.
- **No time-based scheduling.** Dispatch is always an API call or a reconciliation
  command; there is no scheduler.
