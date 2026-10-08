# Jobs and Pipelines

> Based on `packages/sceneops-core/sceneops_core/pipelines/builtin.py`,
> `apps/worker/sceneops_worker/{pipelines,jobs,tasks}/`,
> `packages/sceneops-execution/` (the services, dispatch, leases and recovery both
> processes share) and `apps/api/app/platform/` (the routes). Decision records:
> [ADR-009](../adr/009-job-centric-execution-and-durable-boundaries.md),
> [ADR-010](../adr/010-job-ownership-lease-and-fencing.md) (ownership lease),
> [ADR-011](../adr/011-state-derived-redispatch.md) (lost messages).

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

### Job state transitions

Every change of a Job's status is one PostgreSQL statement whose `WHERE` clause
states the row it applies to, so an actor holding a stale copy of the Job writes
nothing instead of moving it backward or erasing another actor's columns
(`sceneops_db/postgres/jobs.py`).

| Transition | Writer | Guard |
| --- | --- | --- |
| (none) → `PENDING` | `create_job` (API) | `INSERT ... ON CONFLICT DO NOTHING` on `uq_jobs_execution_key_in_flight` |
| (none) → `QUEUED` | the orchestrator, for a task's Job | insert (task Jobs carry no execution key) |
| `PENDING` / `QUEUED` / `FAILED` → `QUEUED` | a dispatch (`mark_queued`): first dispatch, redispatch, retry | `queue_if_unchanged`: `WHERE status = <as read> AND retry_count = <as read> AND lease_generation = <as read>`; sets `queued_at`, and `enqueued_at` unless the Job was already `QUEUED`; a retry increments `retry_count` and is refused past `max_retries` or beside another in-flight Job of its key |
| `PENDING` / `QUEUED` → `RUNNING` | `JobRunner` | `claim_for_run`: `WHERE status IN (PENDING, QUEUED)`; increments `lease_generation`, sets `worker_id` and `lease_expires_at = now() + lease` |
| `RUNNING` → `RUNNING` / `SUCCEEDED` / `FAILED` | `JobRunner`, the claim holding the Job | `update_owned_run`: `WHERE status = RUNNING AND lease_generation = <its claim>` |
| `RUNNING` → `RUNNING` (lease renewal) | the claim's `JobLeaseKeeper` | `renew_lease`: `WHERE status = RUNNING AND lease_generation = <its claim>`; sets `heartbeat_at`, `lease_expires_at` |
| `RUNNING` → `QUEUED`, or `FAILED` (`JobLeaseExpired`) once the claim budget is spent | job lease recovery | `reclaim_expired_lease`: `WHERE status = RUNNING AND lease_expires_at < now()` and `lease_generation <` (or `>=`) the budget; a requeue sets `queued_at` and `enqueued_at` |
| `PENDING` / `QUEUED` / `RUNNING` → `FAILED` | reconciliation (`JobAbandoned`) | `abandon_if_inactive`: `WHERE status IN (...) AND <no activity since the threshold>` |

A status recurs only through a way back to an earlier one: a retry (`FAILED` →
`QUEUED`, which increments `retry_count`) or lease recovery (`RUNNING` → `QUEUED`,
after a claim that incremented `lease_generation`). `(status, retry_count,
lease_generation)` therefore identifies a point in a Job's history and a dispatch
decided on an older one cannot apply. A dispatch whose conditional write
matches nothing raises `JobDispatchConflictError`: nothing is committed and no message
is sent (`POST /jobs/{id}/execute` answers 400). A caller may also state the status its
decision was based on: robot-run registration dispatches only a Job it saw `PENDING`,
so two submissions sharing one Job send one message, and the one that loses answers
with the Job as it is now. `SKIPPED` and `CANCELLED` exist in `JobStatus`, but no
Job transition currently produces them.

### JobRunner (`sceneops_worker/jobs/runner.py`)

1. `claim_for_run` is one atomic `UPDATE ... WHERE status IN (PENDING, QUEUED)
   RETURNING`, so a duplicated or late Celery message cannot run a Job twice. A Job
   that cannot be claimed (running, terminal, cancelled, or claimed first by another
   message) makes `run` raise `JobNotClaimableError`; the Celery task logs one
   `job claim refused` warning and returns `not_claimed`, because under at-least-once
   delivery a refused duplicate is expected, not a failure. A missing Job still fails
   the task. The claim takes the next `lease_generation` and a lease (below), and its
   `LOCKED` JobEvent records the claim (`attempt` = its generation) and the process
   holding it (`worker_node`: Celery's `<name>@<host>`; `pid`): `worker_id` names the
   message, not the worker.
2. The Job is committed as `RUNNING`, the handler runs, and the terminal state is
   committed. A handler failure is a persisted `FAILED` Job (with its error), not an
   exception: the returned Job is the outcome. Every write of a running Job is one
   conditional `UPDATE ... WHERE status = RUNNING AND lease_generation = <its claim>`
   (`update_owned_run`), so a Job leaves `RUNNING` exactly once per claim and a
   terminal Job is never rewritten. A worker whose claim is gone raises
   `JobOwnershipLostError` and writes nothing more. The JobEvents of the terminal
   state are recorded after its commit and are best effort: a failure to write them is
   logged and cannot change the Job.
3. If the Job belongs to a PipelineRun, `JobRunner` then sends one `advance` message.

There is no Celery-level retry on either task. Retrying is always an explicit
redispatch of durable state, or lease recovery's requeue of a Job whose worker is gone.

### Ownership: claim, lease, fencing (`sceneops_execution/jobs/lease.py`, `.../jobs/lease_recovery.py`)

A claim owns a `RUNNING` Job. Three separate mechanisms keep that ownership correct:

| Mechanism | Column | Answers | Written by |
| --- | --- | --- | --- |
| Fencing token | `lease_generation` | *which* claim may write the Job | incremented by every claim; required by every write of the claim |
| Lease | `lease_expires_at` | *until when* the claim holds the Job without news from its worker | the claim (`now() + lease`), each renewal |
| Heartbeat | `heartbeat_at` | *when* the worker last reached PostgreSQL | the claim, each renewal, the terminal write |

`worker_id` is `celery:<task id>`, the Celery message, and a redelivery of the message
carries the same id; it names the worker for diagnosis and never decides ownership.

- **Renewal.** `JobLeaseKeeper` renews the lease every third of
  `SCENEOPS_WORKER_RUNTIME__JOB_LEASE_SECONDS` (60 s) from a thread with its own event
  loop and connection, so a handler that blocks the worker's event loop keeps its lease.
  A renewal proves that the process is alive and reaches PostgreSQL, not that the
  handler makes progress. A renewal that finds the claim gone cancels the handler; a
  renewal that fails (connection error) is retried and is not a loss.
- **Recovery.** The lease sweep of execution recovery (below) reclaims every `RUNNING`
  Job whose lease passed by PostgreSQL's
  clock: the same Job goes back to `QUEUED` and a new job message is sent, until the Job
  has been claimed `JOB_CLAIM_BUDGET` (3) times; then it is `FAILED` with
  `JobLeaseExpired` and, if pipeline-owned, its run is advanced to observe the failure.
  `retry_count` is not touched: it counts retries after failures. A requeued pipeline
  Job is the same Job its task run waits on, so the run continues when it finishes.
- **Expiry is not revocation.** Until recovery reclaims the Job, its claim may still
  renew and finish it; whichever of a renewal and the reclaim reaches the row first
  decides, and PostgreSQL re-evaluates the loser's `WHERE` clause.
- **What fencing covers.** Job writes only. Domain writes a reclaimed handler already
  made (objects, records) are not fenced; they are safe because artifacts are
  write-once and content-pinned and registrations converge (§7), so the next claim's
  execution converges on them.

A recovery pass is stateless, safe at any frequency and concurrently: of concurrent
passes exactly one reclaims a Job and sends its message.

### Messages are wake-ups; execution recovery re-sends lost ones (`execution/recovery.py`)

The two Celery messages carry an identifier and nothing else: `run_job(job_id)` and
`advance(pipeline_run_id)` tell a worker to act on state PostgreSQL already holds. A
lost message loses no information, only the wake-up, and the state says which wake-up
it waits for:

| Durable state | Waits on | Re-sent when |
| --- | --- | --- |
| Job `QUEUED` | `run_job(job_id)` | `queued_at` (its last dispatch) is older than the resend threshold; `enqueued_at` keeps the start of the wait |
| PipelineRun `QUEUED` | `advance(run_id)` (start) | `updated_at` (its last dispatch) is older than the threshold |
| PipelineRun `RUNNING` whose `RUNNING` task's Job is terminal or missing | `advance(run_id)` (observe the Job) | the Job finished, and the run was last stepped or re-sent, longer ago than the threshold |

These are all the states that need a message to progress: every dispatch (API,
orchestrator, lease recovery) leaves its Job `QUEUED`; after every committed
orchestration step a run is terminal or waits on exactly one in-flight Job; a
`RUNNING` Job waits on its worker, whose loss the lease sweep turns into a `QUEUED`
Job. A `PENDING` Job has not been asked to run and waits on nothing.

`sceneops-worker recover` (`make execution-recovery`; looped by `make recovery-up`) is
one pass: the lease sweep, then the `run_job` sweep, then the `advance` sweep. The
threshold is `--resend-after-seconds` (300 s). Before sending, a pass moves the row's
`queued_at` / `updated_at` to now with a conditional `UPDATE` on the value it read and
commits; of concurrent passes exactly one wins, no lock is held during the send, and the
next resend is due one threshold later. A pass that dies before its send leaves a row
that is due again; one that dies after it causes at most one more message.

The sweep cannot see the broker, so a message still waiting behind a backlog looks lost
and is sent again once per threshold: delivery is at least once. Duplicates are
harmless because the consumers are idempotent (one claim per Job, one row-locked step
per run). A dispatch is complete when the state stops waiting (the Job is claimed, the
run steps); there is no outbox and no delivered marker.

### Idempotency and retry

| Concern | Current implementation |
| --- | --- |
| Request identity | `execution_key = sha256(kind, type, dataset_id, dataset_version, model_id, model_version, params)` (`sceneops_core.executions.compute_execution_key`). Creating a Job or PipelineRun whose key already has a `PENDING` / `QUEUED` / `RUNNING` / `SUCCEEDED` record returns that record. `force: true` skips the reuse of a succeeded record: a PipelineRun is always new, a Job is new unless one of its key is still in flight, which is returned. Some job types normalize their params first so equivalent requests share a key (`params_for_execution_key`). |
| Concurrent Job creation | At most one Job per execution key is `PENDING` / `QUEUED` / `RUNNING`: the partial unique index `uq_jobs_execution_key_in_flight` enforces it in PostgreSQL. `create_job` inserts with `ON CONFLICT DO NOTHING` against that index, so concurrent requests for one key converge on one Job and one `CREATED` event; a loser waits for the winner's transaction and returns its Job. Finished Jobs are outside the index, so a key keeps any number of succeeded and failed Jobs. The reuse of a succeeded Job is a read, not a constraint: a request whose lookup preceded a Job's whole run can start one more. PipelineRun creation has no such index; concurrent identical PipelineRun requests can each create a run. |
| Standalone Job retry | Redispatching a `FAILED` Job (`POST /jobs/{id}/execute`) increments `retry_count`; past `max_retries` it is refused, and so is a retry while another Job of its key is in flight. Concurrent retries of one failure spend one retry: one of them queues the Job, the others are refused. |
| Pipeline retry | Re-executing a `FAILED` or `BLOCKED` run resumes at its first unfinished task; succeeded and skipped tasks are not re-run. |
| Duplicate delivery | The atomic Job claim and the row-locked, idempotent orchestration step make a duplicated message harmless. Celery delivers at least once: with `task_acks_late`, a message whose worker died unacknowledged is delivered again with the same task id (immediately when only a pool child died, `task_reject_on_worker_lost`; when the whole worker died, only once another running worker restores it after the Redis `visibility_timeout`, 1 h). A redelivery that finds the Job `RUNNING` or terminal is refused; one that finds it `QUEUED` after lease recovery may run it, under a new claim. |
| Worker loss | The lease passes and lease recovery requeues the Job (see Ownership above); the old claim is fenced by `lease_generation`. |
| Lost message | Execution recovery re-sends `run_job` / `advance` from the waiting state (see above), at least once per resend threshold while the state waits. |
| Duplicate output | Derived artifacts are write-once and content-pinned, so a Job that re-runs converges on the same objects and records (§7). |
| Backfill | None: there is no time-partitioned execution; one more PipelineRun is dispatched for the scope. |

Implementation: `packages/sceneops-core/sceneops_core/executions/key.py`,
`packages/sceneops-execution/sceneops_execution/jobs/service.py` (`create_job`, `mark_queued`),
`packages/sceneops-db/sceneops_db/postgres/jobs.py` (`create`, `queue_if_unchanged`),
`packages/sceneops-execution/sceneops_execution/pipelines/service.py` (`create_pipeline_run`, `validate_executable`).

### Durable-state-first boundaries and failure windows

Every dispatch commits durable state first and sends the message second, so a
message never refers to a row that is not committed. A send that fails, a process that
dies between the commit and the send, and a message the broker accepted and then lost
all leave state that waits; execution recovery re-sends the message (above):

```text
API dispatch     mark QUEUED + commit | send_task | commit ExecutionRecord
                   send fails          the API answers 500; the Job stays QUEUED and is
                                       re-sent after the threshold
                   record commit fails message in flight with no ExecutionRecord (audit only)

Orchestrator     create Job + task RUNNING, commit | send_task | commit ExecutionRecord
                   send fails          the step raises; the Job stays QUEUED and is re-sent

JobRunner        claim + RUNNING | handler | terminal state commit | send advance
                   worker dies after the claim  lease recovery requeues the Job
                   advance not sent             the run waits on a terminal Job; the
                                                advance is re-sent

Lease recovery   reclaim (QUEUED) + commit | send_task | commit ExecutionRecord
                   send fails          the Job stays QUEUED and is re-sent
```

`make worker-advance-pipeline PIPELINE_RUN_ID=…` takes a single `advance` step by hand,
and a `QUEUED` Job can be dispatched through `POST /jobs/{id}/execute`; both are the same
idempotent operations recovery performs.

### Execution health (`sceneops_db/postgres/execution_metrics.py`)

Execution health is derived from the durable state above by read-only SQL; nothing
writes a metric. `sceneops-worker execution-status` (`make execution-status`) prints
one JSON snapshot ([ADR-012](../adr/012-execution-health-from-durable-state.md)):

| Reading | Definition | Cost follows |
| --- | --- | --- |
| backlog, by Job type | `QUEUED` count; oldest queued = `now() - min(enqueued_at)` | Jobs in flight |
| running, by Job type | `RUNNING` count; expired leases; heartbeat age = `now() - min(heartbeat_at)` (renewed every third of the lease, so it stays below that while workers live) | Jobs in flight |
| what active runs wait on | `run_queued`, `job_queued`, `job_running`, `job_finished_awaiting_advance`, with the oldest wait | runs in flight |
| per Job type, over a window | finished, failed, failure rate, failures by error type, throughput; p50 / p95 / p99 / max of queue wait (`locked_at - enqueued_at`), execution (`finished_at - locked_at`), end to end (`finished_at - created_at`); Jobs claimed more than once | the window (`ix_jobs_finished_at`) |
| recovery, over a window | lease requeues, lease-budget failures, job resends (their JobEvents), reclaims by the worker that held the expired claim (`worker_node` of its `LOCKED` event) | the window |
| per pipeline task, over a window | queue wait, execution, and handoff (`task.finished_at - job.finished_at`: the `advance` message and the step that observed the Job) | the window (`ix_pipeline_runs_finished_at`) |
| broker (optional) | messages in the job and pipeline queues | one broker call |

Queue time is measured from `enqueued_at`, never from `queued_at`: recovery moves
`queued_at` on every resend, so a wait measured from it cannot exceed the resend
threshold. Queue wait and execution describe the claim that finished a Job; time lost
to a dead worker appears in end to end and in "claimed more than once". The broker
counts messages and PostgreSQL counts work: resends make the broker's depth larger than
the backlog, a lost message makes it smaller.

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
(`packages/sceneops-execution/sceneops_execution/jobs/service.py`) and when the orchestrator plans a task
(`apps/worker/sceneops_worker/pipelines/planning.py`), so every Job persists a
`steps` list matching its type.

Only the first step is updated at runtime. `JobResultRecorder`
(`packages/sceneops-execution/sceneops_execution/jobs/result_recorder.py`) marks the first `PENDING`
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

These are the verified gaps and bounds of the current design.

- **Worker loss and lost messages are recovered only while execution recovery runs.**
  It is a command (`make recovery-up` loops it), not part of the workers; without it a
  Job whose worker died stays `RUNNING` and state whose message was lost waits. A lost
  worker is noticed within one lease (60 s), a lost message within the resend threshold
  (300 s), each plus the loop interval.
- **Waiting messages are re-sent too.** Recovery cannot tell a lost message from one
  waiting behind a backlog: while Jobs wait longer than the threshold (no worker, a long
  queue), each gets one more message per threshold. Each costs a worker one refused
  claim.
- **A lease detects a dead or unreachable worker, not a stuck handler.** A handler that
  hangs in a live process keeps renewing its lease. A worker paused or cut off from
  PostgreSQL for longer than the lease loses its Job although it is alive; it is fenced
  and its work runs again.
- **Domain writes are not fenced.** A reclaimed handler that is still running may
  write objects and records until it notices the loss (at its next renewal, then its
  handler is cancelled); correctness rests on those writes being idempotent (§7).
- **Dispatch and database are not one atomic unit.** A message can be sent without its
  `ExecutionRecord` being committed, and a record can be committed for a message the
  broker then lost; `ExecutionRecord` is an audit of sends, not of deliveries.
- **Blocking broker sends in async code.** `Celery.send_task` is synchronous; the API's
  `dispatch_job` / `dispatch_pipeline` and the worker's `ExecutionDispatcher` call it
  from `async` code, so the event loop waits for the broker round trip.
- **Strictly serial pipeline scheduling.** One task's Job is in flight per run;
  `depends_on_pipeline_task_ids` is not a scheduling graph.
- **No cancellation.** `CANCELLED` statuses exist, and re-execution and `JobRunner`
  refuse a cancelled record, but no API or worker path sets one.
- **No time-based scheduling.** Dispatch is always an API call or a reconciliation
  command; there is no scheduler.
- **Execution health is read on demand, not exported.** Nothing samples
  `execution-status` or alerts on it. Re-sent `advance` messages and refused claims
  leave no row: they are counted only in recovery's JSON summary and the workers'
  `job claim refused` warnings. Worker capacity (slots) is not recorded, so
  saturation is read as `RUNNING` at its plateau with a growing backlog. Durations mix
  the workers' clocks (`locked_at`, `finished_at`) with PostgreSQL's (`enqueued_at`,
  `heartbeat_at` renewals); across hosts they include clock skew.
