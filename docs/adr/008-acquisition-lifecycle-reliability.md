# ADR-008: Acquisition Lifecycle Reliability

## Status

**Implemented — Closed by Amendment 12.6 (§8).**

Ratified 2026-10-05 with four clarifications, incorporated in place: reconciliation
scheduling (§3.2), capture receipt semantics (§4.2), the retry budget across
replacement Jobs (§5.2, §5.3) and deferral of the stall threshold to Phase 12.4
(§5.3, §8, B4; decided in Amendment 12.4). Amendment 12.5 records the places where §6
needed an interpretation to be implemented. Amendment 12.6 records those of §7, the
full-lifecycle acceptance, the audit of every requirement of this ADR and the
non-blocking limitations that remain. The amendments are the implementation record:
the numbered sections state the decisions, §1 and §2 describe the repository as it
was audited, and the active contract is in
[Robot data ingestion](../workflows/robot-run-and-mcap.md) §3.2.

**Amendment 12.3 — read-only classification (implementation step 12.3).**
Accepted. Step 12.3 adds `ArtifactStore.list_objects`, a published-object scan,
a capture-volume scan and the stateless `reconcile --once`. It changes no
decision above; it fixes where the observations live and what the report calls
things, because §5.1 left both open:

- **Where observation lives.** The published-object scan and its classification
  are in `sceneops-core` (`robots/published_scan.py`) so the API reconciler uses
  them without depending on the Publisher. The capture-volume scan is the
  Publisher's (`python -m sceneops_integrations.recording scan-capture`, DB-free)
  and emits a `CaptureScanReport`; the reconciler accepts that JSON
  (`--capture-report`). The platform never mounts the capture volume and the
  API imports nothing from `ros2/` (§7.4's joined view stays DEFERRED for a
  mounted volume; the report is the boundary). There is no combined Publisher
  `scan` command yet.
- **Report vocabulary.** One state per `run_id`; §5.1 names map as follows.

  ```text
  capture_unfinished                        capture_unfinished
  finalized_no_receipt                      finalized_no_receipt
  finalized_unpublished                     publish_pending
  publishing_incomplete, unpublished_       publication_incomplete   (reasons carry the
    recording_no_source, malformed manifest   §5.1 name and whether a capture can resume it)
  published_unregistered                    registration_pending     (no Job in flight)
  registration_pending                      registration_active      (Job pending/queued/running)
  registration_stalled                      registration_stalled_candidate
  registration_failed_transient/_permanent  registration_failed_transient/_permanent
  registered                                registered
  registered_conflict                       permanent_conflict
  inconsistent (and corrupt inputs)         integrity_incident       (reasons name the contradiction)
  ```

- **Stall threshold stays undecided (§5.3, B4).** Classification accepts an
  optional caller-supplied threshold and, with none, never reports
  `registration_stalled_candidate`. The command supplies none. *(Decided in
  Amendment 12.4: the command now supplies it.)*
- **Failure classes (§5.2)** are encoded in `sceneops-core`
  (`robots/registration_failures.py`) as exception-class names, tied to the
  worker's real exception classes by a test. Classification counts failed Jobs
  per execution key but enforces no budget. *(Enforced from Amendment 12.4.)*
- **What is verified.** A run that is published but not registered has its
  recording bytes (size, sha256) compared to the manifest, because registration
  would reject a mismatch. A registered run is not re-hashed: its listing size
  and its ArtifactRecords are compared to the manifest. A matching
  RobotRunRecord is `registered` regardless of any Job, unless one of those
  facts contradicts it, which is an `integrity_incident`. A Job's success never
  makes a run registered.
- **Reads.** PostgreSQL is read in one `READ ONLY` transaction through two
  batch queries (`PostgresRobotRunRepository.get_many`,
  `PostgresJobRepository.list_for_execution_keys`); no schema change. A run
  that only PostgreSQL knows (a RobotRunRecord with no object under the scanned
  root) is not discovered here; that reverse check belongs to §6.1 (step 12.5).

**Amendment 12.4 — bounded recovery (implementation step 12.4).**
Accepted. Step 12.4 turns the read-only model of 12.3 into recovery, still
stateless and without a lifecycle table. It changes no decision above. It decides
the stall threshold (§5.3, B4) from measurement, fixes what each state is
allowed to cause, and settles the places where §5 left a case open.

*Measured registration latency* (`scripts/dev/benchmark_registration_latency.py`;
through `POST /robot-runs:register` → Job → Celery → `register_robot_run`;
fresh `run_id` per registration so the R5 "already registered" shortcut cannot
apply):

```text
date          2026-10-05          git HEAD  6630a63 (+ uncommitted 12.4 work; the measured path is unchanged by it)
branch        feat/operational-reliability
environment   one macOS host, Docker VM 7.65 GiB; worker-jobs prefork, concurrency 4; PostgreSQL 16,
              Redis 7, MinIO RELEASE.2024-11-07 on the same host (no network latency to storage)
workload      repo fixture MCAPs; the real nuScenes scene-0061 recording of the canonical baseline
              (355,793,127 B); synthetic 2x and 3x end-to-end repetitions of that recording

recording                     size        n   execution (started→finished)   queued→started   worker peak RSS
std_msgs_string               4 KB        5   0.04 s  (max 0.07 s)           ≤ 0.11 s          0.6 GiB
nav_msgs_odometry             13 KB       5   0.05 s  (max 0.07 s)           ≤ 0.06 s          0.6 GiB
can_replay_scene_0061         1.4 MB      5   0.05 s  (max 0.06 s)           ≤ 0.04 s          0.6 GiB
nuScenes scene-0061           355.8 MB    3   1.74 s  (max 1.92 s)           ≤ 0.09 s          0.9 GiB
nuScenes scene-0061 x2        711.6 MB    1   3.55 s                         ≤ 0.09 s          1.3 GiB
nuScenes scene-0061 x3        1,067.4 MB  1   5.44 s                         ≤ 0.09 s          1.6 GiB
5 x scene-0061 submitted at once (4 worker slots)
                              355.8 MB    5   1.66–2.24 s                    0.1 s … 2.23 s    1.9 GiB
```

- **Normal completion** is linear in recording size: about 5 ms/MB (≈195 MB/s)
  on local MinIO. Fixed overhead (claim, manifest read, one PostgreSQL
  transaction) is ≈ 0.04 s. Recording size does matter materially, and so does
  memory: registration holds the whole recording in the worker (≈ 1× its size
  above the idle footprint, B9).
- **Queue / dispatch latency** is ≤ 0.11 s on an idle worker; a fifth job behind
  four busy slots waited 2.23 s, about one registration time. A backlog of B jobs
  waits ≈ B / 4 × the registration time.
- **Heartbeat.** `heartbeat_at` is written at claim and at finish only
  (`heartbeat_at == finished_at` on every completed Job); it never advances while
  a registration runs, so a Job's "last activity" is its claim time.

*Chosen stall threshold: 900 s (15 min), configurable* (`--stall-threshold-seconds`
or `SCENEOPS_API_RECONCILER__STALL_THRESHOLD_SECONDS`;
`DEFAULT_STALL_THRESHOLD_SECONDS`). The rule it must satisfy is the one in §5.3:
exceed the worst legitimate registration time, queue wait included, because an
abandon spends one of the logical registration's three attempts.

```text
slowest measured registration           5.4 s    (1.07 GB)               900 s ≈ 165x
extrapolated to the 5 GB single-PUT limit (B9)   ≈ 26 s                  900 s ≈ 35x
the same 5 GB over storage ~30x slower than local MinIO (≈ 6 MB/s)       ≈ 830 s   still below 900 s
queue wait, 5-way burst                  2.2 s                          900 s ≈ 400x
```

The cost is asymmetric: a threshold that is too short abandons a healthy
registration (and after three of them stops recovering it); one that is too long
only delays recovery of a dead one. 900 s is therefore deliberately far above
everything measured. It is measured on local storage only; deployments with
slower storage or longer queues must measure and raise it. Recovery of W7 / W8 is
consequently not faster than the threshold plus one polling interval.

*What the reconciler may do* (`reconcile --once --apply`; without `--apply` the
command is unchanged and read-only):

| State | Automatic action |
| --- | --- |
| `registration_pending`, no Job at all | submit `REGISTER_ROBOT_RUN` |
| `registration_failed_transient`, attempts remain | submit again |
| `registration_stalled_candidate`, attempts remain | abandon each stalled Job (`FAILED` / `JobAbandoned`) by one conditional UPDATE, then submit a forced replacement |
| `registration_stalled_candidate`, the abandon spends the last attempt | abandon only; no replacement |
| `registered`, `permanent_conflict`, `integrity_incident`, `registration_failed_permanent` (a spent budget included), `registration_active`, every publication and capture state | none |
| `registration_pending` whose newest Job is `SUCCEEDED` or `CANCELLED` | none; reported as skipped (`succeeded_job_without_robot_run`, `latest_job_cancelled`) |

Everything goes through the API's Job path (`RobotRunRegistrationService.submit`,
which gained `force`); the Worker handler is unchanged and the reconciler writes
no object, RobotRunRecord or ArtifactRecord (L-2).

*Clarified retry semantics:*

- **Attempts.** One attempt is one `FAILED` Job of the execution key. Abandoned
  and replacement Jobs share the count; a new row never resets it; success ends
  recovery. Classification enforces it: a transient failure with no attempts
  left is `registration_failed_permanent` with reason `attempt_budget_exhausted`;
  a stalled Job with none left is reported, not abandoned. An operator's forced
  submission after exhaustion is outside the budget and the reconciler does not
  touch it.
- **Replacement is single-winner.** Of any number of concurrent passes exactly
  one changes the stalled row (the UPDATE re-checks "in flight and inactive" on
  the locked row) and only that pass creates the replacement. Plain submission
  (a first Job, a transient retry) is not serialized: concurrent passes may each
  create a Job. That is W11 and remains harmless; it can overshoot the budget by
  at most the number of concurrent passes.
- **A success without a RobotRunRecord and a cancelled Job** are not retried: the
  first contradicts the durable facts (L-9, and a plain submission would only
  deduplicate onto it), the second is an operator's decision.
- **Dispatch failure.** The Job is committed before dispatch. If the broker
  refuses it, the Job stays `PENDING` / `QUEUED`, the action is reported
  `dispatch_failed` (with the Job id), nothing is rolled back and nothing is
  reported as done. Classification and the observe-only pass never touch Redis;
  a later pass recovers the Job as a stalled Job once it has been inactive for
  the threshold. The reconciler bounds broker connect and socket waits to 5 s so
  an unresponsive broker cannot hang a pass.
- **Per-pass bound.** One pass performs at most 100 mutating actions
  (`actions_deferred` reports the rest); an action that raises is recorded and
  the pass continues.
- **Evidence in the report.** Each run's `registration` carries
  `failed_job_count`, `abandoned_job_count`, `attempt_budget` and
  `attempts_remaining`; `--apply` adds `mode`, `policy` and `actions`. States are
  always the facts observed before any action.

*`publish-pending`* (`python -m sceneops_integrations.recording publish-pending
--capture-root <root>`, DB-free): for every finalized capture with a valid
receipt whose publication is absent, or is a recording without a manifest, it runs
`publish_from_capture`. It skips a complete publication, a capture without a
receipt, an unusable receipt, an unfinished capture and a manifest that
contradicts its recording; it never overwrites an object, and a publish that
raises (conflicting bytes, a receipt that disagrees with the bytes) is reported
`failed` while the other captures proceed. Exit 0 when nothing failed, 2 when any
publish failed, 1 when the facts could not be read.

*Local unattended operation.* `make recovery-up` (`compose/recovery.yaml`, profile
`recovery`) starts two services whose whole behavior is
`scripts/ops/poll_loop.sh`: invoke the one-shot command, sleep
`RECOVERY_POLL_INTERVAL_SECONDS` (default 60), repeat. `publication-recovery` runs
`publish-pending` on the capture volume (read-only, DB-free image settings);
`registration-recovery` runs `reconcile --once --apply` (no Redis dependency to
start). They hold no state; a Kubernetes deployment would run the same commands
from CronJobs. There is no Celery Beat or general scheduler.

*Validation* (2026-10-05, same host). Unit: 28 recovery tests over doubles, 13
`publish-pending` tests, 10 `abandon_if_inactive` tests on PostgreSQL (including
ten concurrent abandoners with exactly one winner). Fault injection
(`make test-recovery`, 11 tests): real PostgreSQL and MinIO, a throwaway Redis
container and Celery worker subprocesses killed with SIGKILL, the production
registration handler wrapped only to hold at an exact point. Covered: finalized
capture never published (W3), recording uploaded without manifest (W5), manifest
published and never submitted (W6), a queue message lost (W7), a worker killed
before the R8 commit (W8), a registration committed with its completion lost
(W9), transient failures across replacement Jobs and budget exhaustion followed by
an operator-forced success, a permanent failure never retried, Redis stopped and
restored, Redis paused (unresponsive), six concurrent passes over five
acquisitions, and conflicts / integrity incidents left byte-for-byte untouched.
Each ends on one RobotRunRecord and two ArtifactRecords per acquisition. The
compose loops were also run against the live stack: a finalized capture became a
RobotRun in about 5 s with no operator step.

*Known limitations (current).* The threshold is measured on one host with local
storage. Recovery latency for W7 / W8 is at least the threshold. A dispatch outage
longer than the budget times the threshold (≈ 45 min at defaults) exhausts a
registration's attempts although the registration itself never failed; it then
needs an operator's forced submission. Plain-submission races can create duplicate
Jobs. Capture supervision, capture-volume loss and Kafka retention remain as in B1,
B2 and W13.

**Amendment 12.5 — artifact lifecycle classification (implementation step 12.5).**
Accepted. Step 12.5 implements §6 for the `robot_runs/` prefix as a read-only
report (`python -m app.domains.robots.artifact_lifecycle --once`). It changes no
decision above, deletes nothing and adds no table. It is built on `reconcile_once`
(same listing, RobotRunRecords, Jobs and per-run states), not on a second
scanner, and records where §6 left a case open:

- **Reference query.** `referenced(object)` is evaluated with one prefix read,
  `PostgresArtifactRefRepository.list_by_uri_prefix(root)`: an object is
  referenced iff an ArtifactRecord carries exactly its URI. The run prefix groups
  one publication's objects and finds its registration facts; it never decides
  ownership. The store is listed before PostgreSQL is read, so no object is
  called unreferenced because a registration committed after the references were
  read. The reverse race (a publication and its registration landing between the
  two reads) would make a fresh record look dangling, so every dangling finding is
  confirmed with `ArtifactStore.exists`; one whose object has appeared is dropped
  and counted (`unconfirmed_findings`).
- **Reverse checks, and their scope.** RobotRunRecords are enumerated per root
  (`list_run_ids_for_root`: a run belongs to the root when one of its two
  ArtifactRecords has a URI under it), because a RobotRunRecord registered under
  another root says nothing about this listing. The reconciler gained two opt-in
  parameters for this: `include_database_runs` (classify a RobotRunRecord with no
  listed object, which 12.3 left undiscovered) and `verify_registered_recordings`.
  Defaults are unchanged, so recovery is unaffected.
- **Classes of an unreferenced object.** A run whose reconciliation state is
  `integrity_incident` or `permanent_conflict` makes every recording and manifest
  of the run an `integrity_incident`, referenced or not. For an unreferenced
  object: PN-2 states (`registration_pending`, `_active`, `_stalled_candidate`,
  `_failed_transient`) are `pending` at any age, plus PN-4 when a Job is in
  flight; `publication_incomplete` with a recording and no manifest is O1;
  `registration_failed_permanent` is O2; anything else, a malformed manifest, a
  manifest without its recording and a registered run whose records do not
  reference its objects are incidents.
- **Conflicting or malformed publication is an incident, not O2.** §6.2 lists
  `publish_failed` under O2, but the platform cannot observe a failed publish
  (that is the Publisher's report). What it can observe is a publication that
  contradicts itself, and 12.5 treats that conservatively: an incident, never a
  candidate, at any age. O2 is therefore the consistent pair whose registration
  failed permanently, a spent attempt budget included; `registered_conflict`
  cannot be O2 because its objects are referenced and are incidents.
- **Grace periods.** `grace_pending` (24 h) and `grace_orphan` (7 d) are
  configurable (`SCENEOPS_API_ARTIFACT_LIFECYCLE__*`, `--pending-grace-seconds`,
  `--orphan-grace-seconds`; `orphan >= pending`) and stay defaults of the ADR's
  proposal, not measured properties. §6.2 defines a candidate as unreferenced ∧
  ¬pending ∧ age ≥ `grace_orphan` and PN-1 as age < `grace_pending`, which leaves
  `[grace_pending, grace_orphan)` unassigned; it is `pending` with reason
  `within_orphan_grace`, so an object is never a candidate before `grace_orphan`.
  Age is that of the *newest* recording/manifest object of the run, because the
  two live and die together; a modification time in the future counts as zero.
- **Wall-clock dependence is explicit.** `now` is a required input of the
  service, the CLI's `--observed-at` fixes it, and it is part of the report
  (`observed_at`). The same facts and the same `observed_at` give byte-identical
  JSON.
- **PN-3 needs an observed capture volume.** O1 requires "no receipt/bag
  anywhere". Without a capture report that cannot be established, so the object
  is `pending` (`pn3_capture_source_unobserved`) rather than a candidate. PN-3
  applies to O1 only: for O2 the publication is complete and the bag is not what
  would resume it.
- **Registered recordings are compared by size by default.** The listing size is
  checked against the record and manifest; same-size corruption of a registered
  recording is seen only with `--verify-recording-bytes`, which re-reads each
  registered recording whole (like registration, B9). Manifests are always
  verified: they are read anyway.
- **Not classified.** An unreferenced object that matches no known layout is
  listed under `unclassified` and is never a candidate: O3–O5 stay deferred (§6.4).
- **Report.** `entries` (one per object, per dangling ArtifactRecord and per
  RobotRunRecord whose objects are all gone; sorted), `unclassified`, and a
  `summary` of referenced / pending (oldest age, by reason) / orphan candidates
  (by reason and risk) / incidents (by subject and reason) counts and bytes. The
  same underlying damage can appear as more than one incident entry (a dangling
  record and the run whose objects it names); counts are per entry.

Known limitations (current). The reference read is a prefix scan of `artifacts`
(no `uri` index exists); it was not measured beyond the local stack, where it is
immaterial, and an index is added only if it is measured to matter.
Classification can be stale by the time any action runs, so a future deletion
must re-verify §6.3 itself. The report loads each recording whole when
`--verify-recording-bytes` is set. `robot_runs/` only: Scene / Episode / L3
prefixes are not classified.

Validation (2026-10-06, same host, git HEAD 7f737cc + uncommitted 12.5 work).
Unit over doubles: 59 tests (`test_artifact_lifecycle.py`) covering each class,
PN-1..PN-4, both graces and their boundaries, O1/O2, malformed and conflicting
publication, dangling and corrupt references, a stalled / abandoned / replacement
Job, determinism for a fixed `observed_at`, and that nothing is written. Real
PostgreSQL: the two read queries, including LIKE-wildcard literals and the root
scoping. Real PostgreSQL + MinIO vertical
(`test_artifact_lifecycle_vertical_integration.py`): 18 runs and objects built
through the real publisher, registrar and capture scan, damaged the way crashes
damage them, classified into 4 referenced, 7 pending, 5 orphan-candidate and
several incident entries, with durable state byte-identical before and after and
the one-shot command's JSON equal to the in-process report. The vertical found
that the first version of the DB-side scan read RobotRunRecords of every root
and reported the live stack's baseline run, registered under another root, as
having lost its objects; the scan is now per root and the case has a unit and a
real-PostgreSQL regression test.

**Amendment 12.6 — acquisition status, structured records, lifecycle acceptance and
closure (implementation step 12.6).**
Accepted. Step 12.6 implements §7, validates the whole lifecycle under injected
failure and closes the ADR. It changes no decision above and adds no lifecycle
state, table, column or scheduler: the status is derived on demand from the facts
12.3-12.5 already read.

- **`AcquisitionStatus` (§7.1) is a view, not a record.** `python -m
  app.domains.robots.acquisition_status --once` reads the durable facts once (the
  reconciler's listing, RobotRunRecords, ArtifactRecords and Jobs, and the lifecycle
  reference query) and derives one status per run and the aggregates of §7.2. It is a
  pure function of the reconciliation report, the artifact lifecycle report and the
  observation time `observed_at`, which is part of the report: the same facts and the
  same `observed_at` give byte-identical JSON, and a different `observed_at` changes
  only ages. The two reports are built from one observation
  (`classify_reconciled_artifacts` takes an acquisition report that was already made),
  so they cannot disagree about what was read. The capture stages need a `scan-capture`
  report (`--capture-report`), exactly as the reconciler does; without one a run that
  is only on the capture volume is unobservable, and §7.4's joined view stays a join of
  two reports on `run_id`, not a mounted volume.
- **Interpretations of §7.1.** *Stage* is the furthest durable fact (L-1): a
  RobotRunRecord is `registered`; a valid manifest with its recording is `published`;
  a finalized capture or any publication object is `finalized`; a `.partial` directory
  alone is `capture_unfinished`. *Health* and the added `operator_required` are fixed
  functions of the reconciler's state and reasons (table in the workflow document and
  in `derive.py`); a lifecycle entry that is an integrity incident raises any other
  health to `inconsistent`, because the artifact classification sees contradictions
  (a registered run whose objects its records do not reference) that the acquisition
  state does not. `operator_required` is true exactly where no automatic path moves a
  run on. *Failure* is the newest failed registration Job of a run that is not
  registered, with its class, exception type, attempts, budget and attempts remaining;
  §7.1 says "when health = failed", but a run whose transient failure is still being
  retried has a failure worth reporting, so it is present then too, and absent once a
  RobotRunRecord ends the logical registration (a registered run's failed Jobs stay
  visible as `failed_job_count` / `abandoned_job_count`). Only the register stage has a
  durable failure: a failed publish is the Publisher's report, so a capture that keeps
  failing to publish stays `publish_pending` with a growing age. `message_count` is
  the manifest's per-channel sum (the manifest observation gained it), else the
  receipt's; sizes and counts are absent when neither is observed.
- **The operational report (§7.2).** Counts by stage, health and classification;
  runs needing an operator; registrations pending, active, stalled, failed, with the
  attempt-budget use (a histogram of attempts used, runs whose budget is spent,
  abandoned Jobs); the run with the oldest last durable activity for each non-terminal
  state; incident runs by reason and the lifecycle's referenced / pending /
  orphan-candidate / incident objects and bytes; recording bytes and message counts
  where known; and an `attention` list of every stalled, failed, inconsistent or
  operator-required run. `--summary-only` drops the per-run statuses and keeps every
  aggregate. `oldest` is time since last activity, not a verdict that work is lost.
  One `acquisition_status {json}` line with the aggregates is logged for log
  aggregation. No metrics backend is added (§9).
- **Structured records (§7.3).** `publish-pending` and `reconcile --once --apply`
  log one `acquisition_recovery {json}` line per action and one
  `acquisition_recovery_pass {json}` line per pass, to stderr (stdout stays the one JSON
  report): run, stage, action, outcome, `state_before`, `state_after`, job ids, failure
  class, exception type, attempt, attempts used / remaining, `duration_ms`. For
  `reconcile`, `state_after` comes from a second observation made only when the pass
  changed something; it skips the recording byte comparison and a failure to make it
  never fails a pass whose actions already happened (it leaves `state_after` unset).
  `publish-pending` logs publication attempts only: a capture that stays unpublishable
  would otherwise add a line to every pass, so its skips are counted in the pass line
  by reason. The 12.4 records were written to a logger no command configured, so they
  reached no stream; every one-shot command now routes the acquisition logger to
  stderr (the logger alone, not the root logger, which would enable statement
  logging). The records are evidence; nothing reads them back.
- **Acquisition acceptance** (category-C point-in-time record, below).

*Full-lifecycle acceptance* (`tests/infrastructure/test_acquisition_lifecycle_acceptance.py`,
`make test-recovery`).

```text
date          2026-10-06
git HEAD      a5a0c6a (+ uncommitted 12.6 work)
branch        feat/operational-reliability
environment   one macOS host (15.3), Docker VM 7.65 GiB / 8 CPUs; PostgreSQL 16, MinIO
              RELEASE.2024-11-07 and the dev stack on the same host; a throwaway Redis 7
              container and Celery worker subprocesses (concurrency 2) of the test's own
workload      nine acquisitions of the 1.4 MB repository fixture recording, one capture
              volume, one MinIO RobotRun root, one PostgreSQL
configuration stall threshold 5 s (and 600 s for the passes that must act on nothing),
              attempt budget 3, real waits of 6 s and 3 s between passes; commands run as
              subprocesses with their configuration from the environment
result        1 passed in 177 s
```

Captures are built with Capture's own protocol (`prepare_partial_bag_dir`, the receipt
written into `.partial/`, the atomic `finalize_bag`); the Kafka consumption that feeds
Capture is `make ros2-test` and the streaming equivalence journey, not this test. Every
recovery step is a production command; `recovery_worker.py` (registration) and
`recovery_publisher.py` (publication) only add a fault point.

```text
A  broker down at first submission   7 submissions dispatch_failed, Jobs kept, nothing
                                     registered; status answers with Redis down; after the
                                     broker returns, 4 concurrent passes replace each stalled
                                     Job exactly once (one winner per run); registered
B  publisher killed (os._exit)       recording stored, no manifest; status says resumable,
   between recording and manifest    no operator needed; publish-pending reuses the recording
                                     (recording_written=false) and writes the marker
C  manifest write fails once         that capture reported failed, no marker left, the others
                                     published; two concurrent publish-pending later converge
                                     on one manifest
D  worker SIGKILLed mid-             Job running forever; replaced after the threshold;
   registration                      registered after 2 abandoned attempts
F  transient failure on every        3 attempts without success (1 abandoned, 2 failed), then
   attempt                           no further Job however often it runs; failed, operator
G  a different manifest appears      permanent_conflict; 3 passes + publish-pending change no
                                     object and no row; the RobotRun is still the original
H  the recording disappears          integrity_incident; publish-pending does not re-upload
                                     although the capture still holds the bytes; untouched
I  capture killed before finalize    capture_unfinished, never published
J  finalized bag without receipt     finalized_no_receipt, operator_required, never published
```

Verified: a finalized capture reaches a RobotRun with only `--capture-root` (the manifest's
robot, topics and clock come from the receipt and the bytes); every publication retry
converges; each unregistered manifest is submitted; killed and never-executed Jobs are
replaced boundedly and each replacement consumes the shared budget; a broker outage delays
registration and loses nothing; concurrent passes yield one replacement per stalled Job;
conflicts and incidents change no object, no row and no Job across repeated passes; the
final operational report (4 registered, 1 permanent conflict, 1 incident, 1 budget
exhausted, 1 unfinished, 1 without receipt; 4 runs needing an operator) is derived from the
durable facts and leaves them byte-identical; and 6 of the 9 acquisitions end as exactly one
RobotRun with two ArtifactRecords whose `manifest_checksum` equals the manifest published
for them. The recovery records parsed from the commands' stderr carry the before / after
states, `dispatch_failed` outcomes, the abandoned Job ids, the failure class and the
attempt ordinals (for F, one retry: attempt 3, 1 remaining). The first run of the test
failed on a wrong expectation of mine (two retries for F where the outage replacement was
the second attempt), not on the system.

Run against the dev stack's data, `acquisition_status --summary-only` reports the one
registered canonical-baseline run (355,793,127 B, 8,897 messages) as `registered` / `ok`
with no attention items.

*Requirement audit.* Every requirement of this ADR, against the implementation at the
audited HEAD and the evidence that proves it:

```text
requirement                                  status       evidence
-------------------------------------------  -----------  -------------------------------------------------
L-1 stage = fact, no status column           met          no migration in Phase 12; status is derived (12.6)
L-2 fact ownership                           met          reconciler writes only Job rows and events;
                                                          capture volume / store / DB writers unchanged
L-3 at-least-once, idempotent, concurrent    met          concurrent passes and publishers in 12.4 and 12.6
L-4 matching converge, conflicts loud        met          publish conflicts, G / H of 12.6, 12.4 conflict test
L-5 manifest is the publication marker       met          published_scan; recording-only never registered
L-6 recording of record                      met          capture FinalBagExistsError (12.2); H of 12.6
L-7 receipts never override bytes            met          publish --from-capture checks (12.2)
L-8 bounded automatic retry                  met          budget 3 across replacement Jobs; F of 12.6
L-9 no silent repair                         met          incidents / conflicts untouched (12.4, 12.6)
L-10 classification is pure                  met          READ ONLY transactions; status writes nothing
L-11 grace before action                     met          stall threshold, lifecycle graces; capture_unfinished
                                                          has no action, so no grace is needed
L-12 independence                            met          publisher DB-free; classification needs no Redis
A1 capture receipt                           met          12.2
A2 ArtifactStore listing                     met          12.3 (Local, S3/MinIO, paginated)
A3 atomic LocalArtifactStore write           met          12.2
§5.1 every classification and its action     met          12.3 vocabulary; actions 12.4; publish_failed is
                                                          the Publisher's report and log record
§5.2 failure classes, budget                 met          12.4
§5.3 stalled Job abandon + replace           met          12.4; W7 / W8 real faults; D and A of 12.6
§6 classification of robot_runs/ (O1, O2,    met          12.5; O3-O5 are outside §6.4's Phase 12 scope
   PN-1..PN-4, incidents)
§7.1 AcquisitionStatus                       met          12.6 (interpretations above)
§7.2 aggregates                              met          12.6
§7.3 event facts                             met          12.6 (attempt ordinal and budget evidence added)
§7.4 exposure                                met          CLI JSON; the joined view is a join of reports
§8 steps 12.2-12.6                           met          12.2-12.5 amendments; 12.6 above
W1-W14                                       met or       W1 and W13 recover only within Kafka retention and
                                             bounded      by re-running capture (B1, B2); W12 / F6 / F8 matter
                                                          only if the router is deployed (B7); all others are
                                                          closed by a tested resumer
```

Open items of §9 at closure: B3, B4 and B6 are resolved. B1, B2, B5, B7, B8 and B9 are
limits of claims this ADR does not make (unattended capture recovery, Redis queue
durability, router productionization, batch receipts, objects over 5 GB), and are
carried below as non-blocking limitations; none is an unmet requirement.

*Non-blocking limitations at closure.*

- No capture supervisor: a capture killed before finalize is re-run by an operator
  (B1), and recovery of W1 / W13 depends on Kafka retention, which the repository does
  not configure (B2).
- The stall threshold (900 s) is measured on one host with local storage (Amendment
  12.4); recovery of a killed or lost registration is no sooner than the threshold plus
  one polling interval.
- A dispatch outage longer than the attempt budget times the stall threshold (about 45
  minutes at the defaults) can spend the shared budget without the registration having
  failed; an operator's forced submission then registers it.
- Plain submissions are not serialized: concurrent passes can create a duplicate Job
  (harmless: one RobotRun).
- The artifact lifecycle covers `robot_runs/` only; no artifact is deleted, quarantined
  or repaired, and a future deletion must define §6.3 itself.
- Redis durability of the Celery queue is unverified (B5); a lost message is recovered
  by replacement, which makes its frequency, not its safety, the open question.
- The router has no entrypoint, lease or convergence rule (B7); batch acquisition has
  no receipt producer (B8); a single `put_object` limits a recording to 5 GB (B9).
- The capture stages of the status need a capture report; the recovery loops do not
  supply one and the platform does not mount the capture volume.
- Everything is validated on one host and stack with a 1.4 MB recording in the
  acceptance (a 356 MB recording for the stall-threshold measurement). Behavior at
  scale is not measured here and belongs to Phase 13.

Audited at:

```text
branch     feat/operational-reliability
HEAD       9b90f43 feat(acquisition): recover publication from finalized captures
date       2026-10-05
```

This ADR fixes the operational contract for the acquisition lifecycle

```text
capture → finalized MCAP → recording publication → RobotRunManifest
        → REGISTER_ROBOT_RUN → RobotRun
```

so that it is recoverable after crashes, restarts and lost responses. It
changes **no** canonical decision of [ADR-007](./007-canonical-ingestion-architecture.md):
`RobotRunRecord` stays immutable provenance with no status column (§10, §11.1),
the manifest stays the publication marker (§7.2), registration stays
`REGISTER_ROBOT_RUN(manifest_uri)` (§12.1), and the Capture / Publisher /
FastAPI-Worker boundaries stay as they are (§4, §7). It resolves the part of
ADR-007 §11.3 / §26 that was deferred ("discovery/reconciliation of
published-but-unregistered RobotRunManifests", "capture crash recovery",
"artifact garbage collection" — classification only) and records why the
rest of §11.3 (`CaptureSessionRecord`) stays deferred.

**Amendment — acquisition consolidation.**
Accepted. It changes no decision above; it removes an undeployed component and closes
one finding the audit recorded.

- **The continuous router is removed.** `ContinuousCaptureRouter` had no CLI, compose
  service or make target (§1.1); capture is `capture/cli.py` → `run_capture()`, one
  one-shot process per `robot_run_id`. The router, its tests and benchmark are deleted
  (Git history keeps them). F6 and the router half of B7 are void; the router
  productionization item of §9 is withdrawn. Multi-run routing is a question for the
  later Kafka reliability study, not a deferred ADR-008 deliverable. The audit text
  below describes the repository as it was audited and still mentions the router.
- **Airflow is removed.** Statements below that call Airflow an opt-in proof of concept
  describe the repository as it was audited; the Airflow backend no longer exists
  ([ADR-009](./009-job-centric-execution-and-durable-boundaries.md)).
- **F7 is closed for the deployed capture path.** `capture/cli.py` finalizes a capture
  only on the run's `RUN_END`; `--idle-timeout-seconds` is an abort guard, and an
  aborted capture (`RunEndNotObservedError`) finalizes and commits nothing, leaving a
  `capture_unfinished` partial bag. A capture finalized through the CLI therefore always
  records `finalization.reason = explicit_run_end`. The receipt schema is unchanged:
  `run_capture()` called without `stop_on_run_end` (tests, benchmarks) can still record
  `idle_timeout`, `max_messages` or `manual`, and the Publisher does not gate on the
  reason.

As in ADR-007, three labels are used where confusion is possible:

```text
CURRENT IMPLEMENTATION    what the repository does at the audited HEAD
TARGET CONTRACT           what this ADR freezes; implementation must converge on it
DEFERRED                  explicitly out of scope; a direction, not a contract
```

Unlabelled normative statements describe the TARGET CONTRACT.

Audit basis:

```text
branch     feat/operational-reliability
HEAD       003a90d refactor: domain ingestion architecture (#13)
date       2026-10-05
tree       clean at audit start
method     code and test reading only; no test, container or infrastructure was run
```

---

## 1. Context: the lifecycle as it is (CURRENT IMPLEMENTATION)

### 1.1 Stages, owners, durable facts

```text
stage                  owner          durable fact after the stage                   survives
─────────────────────  ─────────────  ─────────────────────────────────────────────  ──────────────────────
Kafka transport        bridge/Kafka   records in a robot_run_id partition            broker retention only
capture in progress    Capture        <root>/.partial/<run_id>/ (+ Kafka offset      volume; offset only
                                      uncommitted)                                   after finalize
finalize               Capture        <root>/<run_id>/<run_id>_0.mcap (atomic        volume
                                      os.replace + dir fsync); THEN offset commit
publish recording (P3) Publisher      {robot_run_root}/{run_id}/recording.mcap       ArtifactStore
publish manifest  (P5) Publisher      {robot_run_root}/{run_id}/                     ArtifactStore
                                      robot_run_manifest.json   (marker, last)
register (R8)          FastAPI/Worker one PG transaction: 2 ArtifactRecords +        PostgreSQL
                                      RobotRunRecord
```

Verified in code:

- **Capture** (`ros2/capture/`): `CaptureSession` is in-memory only. The
  deployed path is `capture/cli.py` → `run_capture()` (`RunScopedCapture`): one
  one-shot container per `robot_run_id`, with a deterministic per-run Kafka
  consumer group. `ContinuousCaptureRouter` exists as a library and in tests
  only: no CLI, compose service or make target runs it. Offsets are committed
  only after validate + atomic finalize; the router never commits past the
  first offset of a non-terminal session. The finalized bag directory holds an
  MCAP and nothing that records `robot_id`, capture source topics, source
  clock, finalization reason, checksum or message count. Those values exist
  only in the `CaptureResult` printed to stdout and in the operator's head.
- **Publisher** (`sceneops_integrations.recording`): DB-free, one-shot CLI
  (`publish`). P1–P5 exactly as ADR-007 §7.2: write-once keys derived from
  `run_id`, "check → write → re-read → verify", identical bytes reused,
  different bytes `RecordingPublicationConflictError`. All publication inputs
  (`--robot-id`, `--robot-platform`, `--source-kind`, `--source-topic`,
  `--source-clock`, `--root-uri`) are CLI arguments supplied by the caller.
  P6 (submit registration) is not implemented; callers `curl` the API.
- **Registration** (`apps/worker/.../robots/registration.py`): R1–R9 as ADR-007
  §12.1. One transaction; `robot_runs.run_id` primary key; unique
  `recording_artifact_id` / `manifest_artifact_id`; deterministic artifact ids
  derived from `run_id`; `manifest_checksum` equality decides no-op vs
  conflict; `IntegrityError` → rollback → re-resolve (R9).
- **Submission** (`POST /robot-runs:register`): creates a Job, commits it, then
  dispatches. `JobService.create_job` deduplicates on `execution_key` over
  statuses `{PENDING, QUEUED, RUNNING, SUCCEEDED}`; `FAILED` is not
  deduplicated. `execution_key` is a plain index, not a unique constraint.
  Only a job still `PENDING` is dispatched by the submit path.
- **Worker**: Celery with `task_acks_late` and `task_reject_on_worker_lost`
  (redelivery after a killed worker), `autoretry_for=(Exception,)`, 3 retries.
  `JobRunner` claims with `UPDATE … WHERE status IN (PENDING, QUEUED)`.
  `heartbeat_at` is written only at job start, step transitions and finish.
- **ArtifactStore** has no listing primitive for "all objects under a prefix":
  `list_json` is non-recursive and JSON-only (local `glob("*.json")`; S3
  `Delimiter="/"`), so `{root}/{run_id}/robot_run_manifest.json` cannot be
  enumerated from `{root}`. `LocalArtifactStore.write_bytes` is a plain
  `Path.write_bytes` (not atomic); `S3ArtifactStore` uses single-request
  `put_object` (atomic per object).
- **No scheduler** (no Celery beat, cron or equivalent) exists in the
  repository. Airflow is an opt-in proof of concept (ADR-004, §34.13 of
  ADR-007).

### 1.2 Manual hand-offs (CURRENT IMPLEMENTATION)

```text
H1  capture exits  → someone decides to publish, and re-types robot_id / topics / clock / platform
H2  publisher exits → someone copies manifest_uri into POST /robot-runs:register
H3  Job stuck (RUNNING / QUEUED) → someone calls POST /jobs with force, or /jobs/{id}/execute
H4  capture killed → someone re-runs capture with the same run_id before Kafka retention expires
```

`scripts/e2e/*` and `make canonical-bootstrap` perform H1/H2 in shell; nothing
does so unattended. This is the "no automatic capture → publish → register
hand-off" limitation recorded in `reserved-and-limitations.md` §7 and ADR-007
§34.13.

### 1.3 Retry / idempotency guarantees already present (and test evidence)

| Guarantee | Mechanism | Evidence (existing tests; not re-run here) |
|---|---|---|
| Crash before finalize rebuilds from Kafka | commit-after-finalize; `.partial` discarded on next attempt | `ros2/capture/tests/test_crash_boundaries.py` boundary C |
| Crash after finalize, before offset commit converges; different content fails loudly | `FinalBagExistsError` + `same_recorded_content` (ignores `log_time`) in `run_capture` | boundary D (both tests) |
| Publisher retry after crash before manifest reuses recording | write-once keys, identical bytes reused | `test_recording_publisher.py::test_retry_after_crash_before_manifest_reuses_recording` |
| Conflicting recording / manifest never overwritten | `RecordingPublicationConflictError` | `test_conflicting_recording_fails_without_manifest`, `test_conflicting_manifest_fails` |
| Identical manifest bytes on identical inputs | canonical serialization | `test_identical_inputs_produce_identical_manifest_bytes` |
| Registration is a no-op for the same manifest; conflict for a different one | `manifest_checksum` rule, R5 | `robots/test_registration.py`, `test_registration_integration.py` (real MinIO/PG) |
| Concurrent registration of one `run_id` converges | PK + unique + R9 | `test_concurrent_registration_of_same_manifest_converges` (real PG) |
| Single-transaction registration: no half-registered run | R8 | ADR-007 §12.1 failure table |

### 1.4 Findings that this ADR must resolve

```text
F1  A finalized bag is not self-describing: the publication inputs are not durable.
    Retrying publish with different arguments yields a different manifest and a
    hard conflict at the write-once manifest key.
F2  Published-but-unregistered manifests are undiscoverable (no listing primitive,
    no DB trace, no automation).
F3  A killed worker leaves a RUNNING registration Job forever: redelivery fails the
    claim ("already running") outside JobRunner's try block, so the Job is never
    marked FAILED; the Celery retries (3) exhaust; execution-key dedup then returns
    the stuck RUNNING Job to every later POST /robot-runs:register for that manifest.
F4  A Job left QUEUED (dispatch failure after the QUEUED commit, or a lost Redis
    message) is returned by dedup and not re-dispatched by the submit path.
F5  LocalArtifactStore.write_bytes is not atomic: a crash mid-write leaves a truncated
    object that the publisher then reads as a *conflicting* write-once object
    (permanent failure until manually deleted). S3/MinIO is not affected.
F6  The router does not apply run_capture's "same content → converge" rule: after a
    crash between finalize and offset commit, replay drives its session to FAILED
    although the finalized bag is intact. Only relevant if the router is deployed.
F7  The idle-timeout fallback can finalize a truncated run as complete; the
    finalization reason lives only in memory and cannot be put in manifest v1.
F8  No lease or lock on the capture volume: a second capture process for the same
    run_id on the same output root deletes the first one's .partial directory.
F9  Reconciliation as a dedup'd Job would return a stale SUCCEEDED report
    (SUCCEEDED is a dedup status). It must not be modelled as such a Job.
```

---

## 2. Failure windows

"Durable state after" is what a restart finds. "Today" is the CURRENT
IMPLEMENTATION behavior. "Resolution" is the TARGET CONTRACT (§4–§6).

| # | Window | Durable state after | Today | Resolution |
|---|---|---|---|---|
| W1 | Capture killed before finalize | `.partial/<run>` stale; offset uncommitted; Kafka records within retention | Re-run capture (H4); `.partial` discarded; nothing supervises or detects it | Classify `capture_unfinished` (scan). Recovery stays "re-run capture"; loss iff Kafka retention is exceeded (Kafka is bounded replay, not canonical storage; ADR-007 §29.14 records that offsets commit only after durable finalization). |
| W2 | Killed after `os.replace`, before offset commit | final bag present; offset uncommitted | `run_capture` converges (tested). Router: session `FAILED`, bag intact (F6) | Unchanged for `run_capture`. First finalized bag is the *recording of record* (L-6). F6 only if the router is deployed. |
| W3 | Finalized, nothing publishes it | final bag on capture volume only; platform has no trace | Invisible; publish inputs not durable (F1) | Capture writes a **capture receipt** atomically with finalize (§4.2). `publish-pending` finds and publishes it. |
| W4 | Killed during recording upload (P3) | S3: key absent. Local backend: possibly truncated object (F5) | S3: retry fine. Local: permanent "conflict" | Atomic `LocalArtifactStore.write_bytes` (tmp + `os.replace`). |
| W5 | Recording uploaded, manifest missing (P3 done, P5 not) | `recording.mcap` only | Harmless; retry reuses it; undetected | Classify `publishing_incomplete` (resumable if receipt/bag exists) or `unpublished_recording_no_source`. Never registered (marker invariant). |
| W6 | Manifest uploaded, registration never submitted | manifest + recording in store; no DB trace | Undiscoverable without knowing the URI (F2) | Platform reconciler lists manifests, finds ones with no RobotRunRecord, submits registration. |
| W7 | Submitted, Job never executes (dispatch failure, lost queue message, worker down) | Job `PENDING`/`QUEUED`; no RobotRunRecord | Dedup returns the Job; submit path only dispatches `PENDING` (F4) | Reconciler detects `registration_stalled` (age beyond threshold, no RobotRunRecord) and re-dispatches or submits a forced replacement Job (idempotent). |
| W8 | Worker killed mid-registration | DB rolled back (R8 is one transaction); Job `RUNNING` | Job stuck `RUNNING` forever (F3) | Same as W7: stalled → replacement. No DB repair needed. |
| W9 | Registration committed, completion or HTTP response lost | RobotRunRecord + ArtifactRecords committed; Job possibly `RUNNING`; client has no answer | Re-POST → dedup returns the existing Job; any rerun is `created=false` | RobotRunRecord is the only truth for "registered". A stalled Job whose RobotRunRecord exists with the same manifest checksum is `registered` (the Job row is noise). |
| W10 | Duplicate publication | identical bytes: no-op. Different bytes: loser fails `Conflict`. Check-then-write race with different bytes: last writer wins on S3 | As ADR-007 §7.2: detected downstream (registration R6, consumers' checksums) | Unchanged. Single publisher per `run_id` remains the assumption; conditional writes stay DEFERRED. A re-capture of a published run is a loud conflict by design (L-6). |
| W11 | Duplicate / concurrent registration | PK + unique constraints → one winner | Converges (tested). Two Jobs can exist: `execution_key` is not unique | Unchanged. Duplicate Jobs are harmless; **no unique index is added** (it would affect every job type). |
| W12 | Capture/router restart with in-flight sessions | in-memory sessions lost; `.partial` stale; offsets never committed past a non-terminal session | Replay rebuilds from Kafka. Router replay can re-create sessions for already-finalized runs and mark them `FAILED` (F6) | Receipt records the finalization reason (F7). Router productionization is a precondition to any claim beyond `run_capture` (§9). |
| W13 | Capture volume lost after finalize, before publish | nothing | Recording lost unless Kafka replay is still available | Receipt does not help (it lives on the volume). Recapture within retention yields different bytes but nothing is published yet, so it is legal. |
| W14 | Final bag modified/corrupt between finalize and publish | bag differs from receipt checksum | Publisher re-validates the MCAP (P1) but has nothing to compare against | Receipt checksum/size mismatch fails publish loudly; the receipt never overrides bytes (L-7). |

---

## 3. Decision

### 3.1 No stateful coordinator, no new state machine

SceneOps does **not** add an acquisition coordinator service, workflow engine,
`AcquisitionRecord` table, or lifecycle status column. Every stage is already
a durable fact owned by exactly one component; the missing pieces are (a) the
publication inputs not being durable (F1), (b) nothing enumerating the facts
(F2), and (c) stalled Jobs (F3, F4).

```text
Operational lifecycle = at-least-once execution
                      + idempotent durable effects (write-once keys, run_id identity,
                        manifest_checksum rule, PK/unique constraints — all existing)
                      + reconciliation (stateless, idempotent sweeps)
```

A **stage is defined by the existence of its fact**, never by a stored status
(this is ADR-007 §11.1 applied to operations):

```text
stage              fact                                                    owner      how it is read
─────────────────  ──────────────────────────────────────────────────────  ─────────  ──────────────────────
capture_unfinished <root>/.partial/<run_id>/ exists, no final bag          Capture    capture-volume scan
finalized          <root>/<run_id>/ with valid capture receipt             Capture    capture-volume scan
published          manifest object at the deterministic key, valid         Publisher  ArtifactStore exists/read
registered         RobotRunRecord(run_id), manifest_checksum == manifest   Platform   PostgreSQL read
```

### 3.2 Components stay where they are; each stage gets one resumer

```text
Capture        recording responsibility. Writes only to its own volume.
               Gains: a capture receipt (§4.2). No DB, no API, no ArtifactStore.
Publisher      ArtifactStore publication. Gains: --from-capture, scan, publish-pending.
               No DB, no API.
FastAPI        registration orchestration. Gains: a read-only reconciler that joins
(+ Worker)     ArtifactStore listing with PostgreSQL, and may submit
               REGISTER_ROBOT_RUN through the existing submission service.
               The Worker handler is unchanged.
```

```text
capture ──(receipt in final bag)──▶ [publish-pending]──▶ manifest in store
                                                              │
                                    [reconciler: list store ∖ RobotRunRecords]
                                                              ▼
                                       REGISTER_ROBOT_RUN (existing Job, existing handler)
```

- `publish-pending` (publisher CLI, DB-free): enumerates finalized bags that
  have a receipt, and runs the existing `publish_recording` with the receipt's
  inputs. Re-running it is the retry.
- `scan` (publisher CLI, DB-free, read-only): classifies capture-volume and
  ArtifactStore facts. It is the only component that sees both sides of the
  capture→publish boundary, because the publisher container is the one that
  already mounts the capture volume read-only.
- The reconciler lives in the **API app** (it already has `ArtifactStoreDep`,
  DB sessions and `RobotRunRegistrationService`). The Worker cannot import the
  API, and submission must go through the API's Job + dispatch facade.
  Reconciliation is **not a Job** (F9); the registrations it submits are.
- P6 (publisher submits registration) stays an optional convenience exactly as
  in ADR-007 §7.2. Correctness never depends on it.

**Scheduling.** SceneOps does not introduce a general-purpose scheduler.

- The reconciliation contract is a **stateless one-shot execution**,
  conceptually `reconcile --once`: read the durable facts, classify, optionally
  act, print the report, exit. It keeps no state between invocations, so every
  invocation is a complete reconciliation. `scan` and `publish-pending` follow
  the same one-shot model. The reconciler logic lives in the API app package
  and is invoked by a one-shot entrypoint there; it does not require a running
  API server.
- Local / docker-compose operation may run a **lightweight polling service**
  that does nothing but invoke the same one-shot contract in a loop (sleep,
  invoke, repeat). It holds no state and adds no behavior; killing it loses
  nothing.
- A future Kubernetes deployment may use a CronJob, or any other
  deployment-level scheduler, invoking the same entrypoint.
- Reconciliation **must not depend exclusively on Celery Beat or Redis**, because
  the broker and workers may themselves be what failed. Classification needs only
  ArtifactStore and PostgreSQL. Submitting a registration still dispatches
  through the existing Job path, which does use Redis/Celery; if that dispatch
  fails, the Job is left `PENDING`/`QUEUED`, which the next invocation classifies
  as `registration_stalled` (§5.1). A Redis outage therefore delays registration
  and never loses it.
- Which of these runs, and how often, is a deployment choice. This ADR requires
  only that the one-shot contract is safe to run at any frequency, concurrently,
  and after any crash (L-3).

### 3.3 Why not the alternatives

- **`CaptureSessionRecord` / durable capture state in PostgreSQL** (ADR-007 §11.3):
  would give capture a DB dependency or a control-plane service. The failure
  windows above are all closed without it because the recoverable truth is
  Kafka (before finalize), the volume (after finalize), the store (after
  publish) and PostgreSQL (after register). It stays DEFERRED; triggers in §9.
- **Publisher calls the API and retries (P6 as the mechanism)**: leaves W6/W7
  open when the publisher is gone, and gives the publisher orchestration
  responsibility. Kept as an optimization only.
- **Registration status columns or a `published` table**: duplicate facts that
  already exist and can drift from them.
- **Unique index on `jobs.execution_key`**: crosses every job type for a harm
  (duplicate registration Jobs) already proven benign.

---

## 4. Durable state: reused vs added

### 4.1 Reused (no change)

```text
run_id                             identity of the whole lifecycle; object-key segment
{root}/{run_id}/recording.mcap     write-once key
{root}/{run_id}/robot_run_manifest.json   write-once key; the publication marker
sha256 / size_bytes                manifest.recording, ArtifactRecord.checksum/size_bytes
robot_run_*_artifact_id(run_id)    deterministic ArtifactRecord ids
robot_runs.run_id PK; artifacts.artifact_id PK; unique recording/manifest artifact ids
RobotRunRecord.manifest_checksum   the conflict rule
Job.execution_key / status / error.type / created_at / heartbeat_at   dedup, stall and failure-class evidence
Kafka committed offset (commit-after-finalize)   capture resume point
```

### 4.2 Added

**A1. Capture receipt (TARGET CONTRACT).** One JSON file inside the capture
bag directory, written by Capture, **before** the atomic rename so that the
directory rename publishes recording and receipt together:

```text
<root>/.partial/<run_id>/   <run_id>_0.mcap  +  capture_receipt.json
        │  write receipt → fsync receipt → (existing) fsync/validate → os.replace → dir fsync
        ▼
<root>/<run_id>/            <run_id>_0.mcap  +  capture_receipt.json
```

```text
schema_version    "sceneops.capture_receipt/v1"   (same field name as RobotRunManifest)
run_id, robot_id
robot_platform    optional, as supplied to capture (null otherwise)
recording         { file, format: "mcap", checksum: "sha256:…", size_bytes }
capture           { source: { kind: "kafka", topics: [sorted] }, source_clock }
message_count, per_channel_counts
finalization      { reason: explicit_run_end | idle_timeout | max_messages | manual,
                    finalized_at (capture-host UTC) }
kafka             { partition, first_offset, last_offset, first_sequence, last_sequence }
```

Rules:

1. The receipt is **immutable acquisition metadata**, required to recover
   publication after finalization. It is written once, atomically with
   finalize, and never modified afterwards. It is **not** canonical Scene or
   Episode metadata, nor canonical provenance: it is never uploaded as
   provenance, never read by registration, and never a source of identity.
   It is **publication input and diagnostics only**. It must never override,
   reinterpret, or manufacture facts from the MCAP payload: time range,
   channels, per-channel counts, checksum and size are always derived from the
   bytes, never taken from the receipt. `RobotRunManifest` v1 is unchanged;
   Kafka offsets stay out of the manifest (ADR-007 §26).
2. `publish` from a receipt re-derives every fact from the MCAP bytes (P1) and
   **fails** if the receipt's checksum, size or message count disagree. The
   receipt supplies only what the bytes cannot: `robot_id`, `robot_platform`,
   capture source, source clock. This makes manifest bytes identical across
   retries and removes argument drift (F1).
3. Its schema is pure Pydantic in `sceneops-core` (layer B); Capture and
   Publisher both depend on `sceneops-core` already. Capture still imports no
   DB, API or ArtifactStore code.
4. A finalized bag **without** a receipt (legacy bags, batch acquisition) stays
   publishable by the explicit-argument path; it is classified
   `finalized_no_receipt` and is not auto-published.

**A2. ArtifactStore listing capability (TARGET CONTRACT).** An additive port
method returning, for every object under a prefix (recursive, paginated),
`(uri, size_bytes, last_modified)`. `last_modified` is the only durable clock
for an unreferenced object and is required by the orphan model (§6). It is a
capability, not state. Implemented for `LocalArtifactStore` (mtime) and
`S3ArtifactStore` (`list_objects_v2`).

**A3. Atomic `LocalArtifactStore.write_bytes`** (temp file in the same
directory + `os.replace`), closing F5. Contract-neutral.

**No PostgreSQL schema change, no migration, no new table.**

---

## 5. Retry and reconciliation invariants (TARGET CONTRACT)

```text
L-1   Stage = fact. A stage is true iff its durable fact exists. No status column, no
      stage table; facts are only ever added, never mutated.
L-2   Fact ownership. Capture writes the capture volume only. The Publisher writes
      ArtifactStore objects only. The platform writes PostgreSQL only. The reconciler
      writes no bytes and no canonical rows; it acts only by submitting
      REGISTER_ROBOT_RUN (or re-dispatching/replacing a stalled registration Job).
L-3   At-least-once, idempotent effects. Every resumer is safe to run any number of
      times, concurrently, after any crash. Effect identity is run_id plus the existing
      checksums. There is no distributed transaction and no lock across components.
L-4   Matching retries converge; conflicting retries fail loudly and are not retried
      automatically. (Existing ADR-007 rule, restated as the contract of every resumer.)
L-5   Publication marker. A recording without a valid manifest is never registered and
      never treated as published. The reconciler never infers a manifest.
L-6   Recording of record. The first finalized bag for a run_id is the recording of
      record. A differing re-capture can neither replace a finalized bag nor a published
      object; after registration it conflicts at the manifest_checksum rule.
L-7   Receipts never override bytes. Every fact in a manifest is derived from the MCAP
      bytes or the receipt's identity fields; a disagreement fails publish.
L-8   Bounded automatic retry. Automatic resubmission applies only to transient failures
      and only within an attempt budget (§5.2). Permanent failures surface as `failed`
      and require an operator.
L-9   No silent repair. Inconsistent canonical state (ArtifactRecord without
      RobotRunRecord, record referencing missing bytes) is reported, never repaired
      (ADR-007 §12.1).
L-10  Classification is pure. Computing a report changes nothing. Actions are a separate,
      explicit step.
L-11  Grace before action. A fact younger than the grace period is `in progress`, not
      stalled or orphaned. Grace is an efficiency and safety margin, not a correctness
      requirement (duplicate execution is already safe).
L-12  Independence. Capture never blocks on the platform; the Publisher never blocks on
      the DB or API; a reconciler outage blocks nothing but automatic registration.
```

### 5.1 Classification and action per window

Per `run_id`, the first matching row wins. "Source" says who can observe it.

| Classification | Condition | Observed by | Action |
|---|---|---|---|
| `capture_unfinished` | `.partial/<run>` exists, no final bag, older than grace | `scan` | none automatic; supervisor/operator re-runs capture (resumable from Kafka) |
| `finalized_no_receipt` | final bag, no receipt, no manifest | `scan` | report; explicit-argument publish only |
| `finalized_unpublished` | final bag + valid receipt, no manifest, no recording object | `scan` | `publish-pending` publishes |
| `publishing_incomplete` | recording object present, manifest absent, receipt/bag present | `scan` | `publish-pending` resumes (reuses recording) |
| `unpublished_recording_no_source` | recording object present, manifest absent, no receipt/bag | `scan` | report; orphan class O1 (§6); never automatic |
| `publish_failed` | publish raises conflict / receipt mismatch / corrupt MCAP | `publish-pending` | permanent; operator |
| `published_unregistered` | valid canonical manifest, no RobotRunRecord, no active Job | reconciler | submit registration (within budget) |
| `registration_pending` | Job `PENDING`/`QUEUED`/`RUNNING` within stall threshold, no RobotRunRecord | reconciler | wait |
| `registration_stalled` | such a Job beyond the stall threshold, no RobotRunRecord | reconciler | re-dispatch, or submit a forced replacement Job; safe because registration is idempotent |
| `registration_failed_transient` | latest Job `FAILED` with a transient error type, attempts < budget | reconciler | resubmit |
| `registration_failed_permanent` | latest Job `FAILED` with a permanent error type, or attempts ≥ budget | reconciler | none; operator |
| `registered` | RobotRunRecord exists, `manifest_checksum` == sha256(manifest bytes) | reconciler | none (terminal) |
| `registered_conflict` | RobotRunRecord exists, checksum differs from the manifest in the store | reconciler | none; operator (ADR-007 §18.4) |
| `inconsistent` | ArtifactRecord(s) without RobotRunRecord, or record whose bytes are missing/mismatched | reconciler | none; incident (L-9) |

`registered` takes precedence over any Job state: a stale `RUNNING` Job next to
a matching RobotRunRecord is `registered` (W9).

### 5.2 Failure classes and attempt budget

Classification uses `Job.error.type` (the exception class name) — no new state:

```text
permanent   RobotRunRegistrationConflictError, RobotPlatformConflictError,
            RecordingVerificationError, PublishedArtifactMissingError,
            InconsistentCanonicalStateError, RobotRunManifestError (and subclasses),
            manifest schema validation errors
transient   everything else (store/DB connectivity, timeouts, OSError, ArtifactReadError)
attempts    count of Jobs that ended without success for the same logical registration
            (see below): FAILED Jobs, including abandoned/replaced ones
budget      3 attempts without success, then `registration_failed_permanent`
            (reason `attempt_budget_exhausted`)
```

The **logical registration attempt** is one `REGISTER_ROBOT_RUN` for one
manifest, identified by its deterministic `execution_key` (type + params, i.e.
the `manifest_uri`). A forced replacement Job (§5.3) computes the same
`execution_key`, so the budget is counted across all Jobs of that key and **is
not reset by creating a new Job row**. A stalled Job that is abandoned counts
as one attempt like any other failure. The budget ends at success; it is never
reset automatically. An operator's explicit forced submission is outside the
budget and is the way to retry after exhaustion. Budget exhaustion never
overrides convergence: if a RobotRunRecord with a matching `manifest_checksum`
exists the run is `registered` (L-4); a differing checksum is a permanent
conflict immediately, with no retries (L-4, `registered_conflict`).

Unknown error types are transient until the budget is spent. The budget is an
internal constant, not public configuration (testing.md §7).
`PublishedArtifactMissingError` is permanent because a manifest implies a
verified recording (L-5) and MinIO/S3 reads are strongly consistent.

### 5.3 Stalled Job handling

A replacement Job is created with `force=true` (bypassing dedup) and a normal
dispatch. Before creating the replacement, the stalled row **must** be moved
to `FAILED` with error type `JobAbandoned` by a conditional update
(`WHERE status IN (PENDING, QUEUED, RUNNING) AND` stalled), restricted to
`REGISTER_ROBOT_RUN` (idempotent by construction). This is what makes each
replacement consume the attempt budget of §5.2. A late completion of the
original worker may overwrite that row; this is accepted because the effect is
idempotent.

**The stall threshold** must exceed worst-case registration time, queue wait
included. It is configurable and defaults to 900 s, derived in Amendment 12.4
from measured registration latency; the number is an implementation default
justified by that measurement, not a frozen part of the contract (blocker B4,
resolved).

---

## 6. Artifact lifecycle contract

This section defines classification only. **Phase 12 deletes nothing.**

### 6.1 Reference model

```text
referenced(object)  ⇔  ∃ ArtifactRecord a : a.uri == object.uri
reverse check       ArtifactRecord whose object is absent        → `dangling_reference` (incident)
                    ArtifactRecord with checksum ≠ object bytes  → `referenced_corrupt` (incident)
```

`ArtifactRecord` is the reference for every kind (recording, manifest,
observation payload, Scene/Episode manifest revisions, reports, tables).
Superseded revisions remain referenced by their ArtifactRecords and are never
candidates (storage-layout: "superseded revisions stay for lineage").
The checksum comparison applies only to records that carry a checksum (not all
writers populate it — `reserved-and-limitations.md` §6).

### 6.2 Three classes

**Referenced** — satisfies `referenced(object)`. Never a candidate.

**Pending (in-progress)** — unreferenced but protected; any of:

```text
PN-1  age < grace_pending                                  (object may belong to an
                                                            in-flight write → register step)
PN-2  recording/manifest of a run classified published_unregistered,
      registration_pending, registration_stalled or registration_failed_transient
      — regardless of age. This is the platform's only copy of the recording.
PN-3  recording object whose capture bag/receipt still exists on the capture volume
      (resumable publication)
PN-4  object under a run/dataset scope that has a non-terminal Job referencing that
      scope (build/register in flight)
```

**Orphan candidate** — unreferenced ∧ ¬pending ∧ age ≥ `grace_orphan`. A
candidate carries a reason and a risk tier; it is **not garbage**:

| Reason | Meaning | Tier |
|---|---|---|
| `O1 recording_without_manifest` | recording present, manifest absent, no receipt/bag anywhere | high — may be the only copy of a recording |
| `O2 recording_manifest_permanently_failed` | `registration_failed_permanent` / `publish_failed` / `registered_conflict` | high — quarantine, human decision |
| `O3 payload_unreferenced` | `observation_payloads/…` object with no ArtifactRecord | low |
| `O4 manifest_revision_unreferenced` | `manifest-{sha}.json` with no ArtifactRecord (crash between write and register) | low |
| `O5 unknown_prefix_object` | object under a managed prefix matching no known layout | medium |

`grace_pending` and `grace_orphan` are internal constants chosen conservatively
(initial proposal: 24 h and 7 d), revisited from measured pipeline durations.

### 6.3 Constraints on any future deletion (not decided here)

A later ADR must define, at minimum: re-verification of `referenced(object)`
at deletion time (classification can go stale), a dry-run, a quarantine step,
and the rule that `robot_runs/**` bytes are never deleted without explicit human
confirmation. The capture volume is outside ArtifactStore classification.

### 6.4 Scope in Phase 12

The reference model, classes and reasons above are the contract for all
prefixes. Phase 12 implements classification for the `robot_runs/` prefix
(O1, O2 and the integrity incidents). Other prefixes (O3–O5) extend the same
rules later without changing the contract.

---

## 7. Observability contract

Minimal **facts**, derived on demand from the durable facts of §3.1. No new
store, no metrics backend. Prometheus, Grafana and OpenTelemetry are DEFERRED;
this contract defines what they would export.

### 7.1 Per-run `AcquisitionStatus` (derived, not stored)

```text
run_id, robot_id (receipt or manifest)
stage            capture_unfinished | finalized | published | registered
health           ok | pending | stalled | failed | inconsistent
classification   one value from §5.1
failure          { stage: capture|publish|register, class: transient|permanent,
                   error_type, attempts }                            (when health = failed)
timestamps       finalized_at (receipt) · published_at (manifest object last_modified)
                 · registered_at (RobotRunRecord.registered_at)
durations        finalize→publish · publish→register · finalize→register
size             recording_bytes, message_count, channel_count
finalization     reason (receipt)
```

Caveat: `finalized_at` is the capture host's clock and `published_at` is the
store's clock; cross-host durations are indicative only.

### 7.2 Aggregates

```text
count by classification / health
oldest age per non-terminal stage (capture_unfinished, finalized_unpublished,
                                   published_unregistered, registration_stalled)
orphan candidates: count and bytes by reason (§6.2)
integrity incidents: dangling_reference, referenced_corrupt, inconsistent
```

### 7.3 Event facts (structured log lines, existing logger)

Every resumer action logs one structured line:
`{run_id, stage, action, outcome, duration_ms, error_type, attempt}`.
Existing sources are reused: publisher result JSON (`recording_written`,
`manifest_written`), Job events (registration start/finish times), Job result
(`created`).

### 7.4 Exposure

The report shape is the contract. Its transport (CLI JSON now; an API endpoint
later) is not decided here. `publish-pending`/`scan` produce the capture→publish
half; the reconciler produces the publish→register half; `run_id` is the join
key. A single joined view needs the capture volume to be visible to the
platform and is DEFERRED.

---

## 8. Implementation order (TARGET; each step independently shippable and verifiable)

```text
12.2  Durable publication inputs
      - CaptureReceipt schema (sceneops-core); Capture writes it inside .partial before
        the atomic rename (fsync order in §4.2); capture CLI accepts --robot-platform.
      - Publisher: `publish --from-capture <bag dir>` with receipt checks (L-7).
      - LocalArtifactStore atomic write_bytes (F5).
      Verify: unit tests for each crash boundary around rename/receipt; receipt tamper;
      real filesystem + real MinIO publish retry; import-boundary tests unchanged.

12.3  Listing + read-only classification
      - ArtifactStore listing capability (Local, S3/MinIO; pagination on real MinIO).
      - Publisher `scan` (capture volume + store). API reconciler classification (store ∪
        PostgreSQL ∪ Jobs) as a stateless one-shot (`reconcile --once`), read-only, no
        submission.
      Verify: every §5.1 row from fixtures; real PG + MinIO; no write occurs (L-10).

12.4  Resumption  (implemented; see Amendment 12.4)
      - Measure registration latency across representative recording sizes and derive the
        stall threshold (§5.3) before any stalled-Job action is implemented.
      - `publish-pending`.
      - Reconciler submission with the per-registration attempt budget (across replacement
        Jobs) and failure classes (§5.2); stalled-Job abandon + replacement (§5.3).
      - Compose polling service that only loops the one-shot entrypoint (§3.2).
      Verify: real Celery/Redis/PG fault injection — kill worker mid-registration (W8),
      lose QUEUED message (W7), kill after commit (W9), concurrent reconcilers (W11),
      kill publisher between P3 and P5 (W5).

12.5  Artifact classification for `robot_runs/`  (implemented; see Amendment 12.5)
      - Reference query, pending rules PN-1..PN-3, O1/O2, dangling/corrupt incidents.
      Verify: PN-2 never classified as orphan at any age; classification is read-only.

12.6  Observability facts + acceptance  (implemented; see Amendment 12.6)
      - AcquisitionStatus report + structured log lines (§7).
      - Fault-injection E2E over the full lifecycle; record it as a category-C
        point-in-time acceptance report; update active docs whose contract changed
        (streaming-transport limitations, robot-run-and-mcap §3.2, storage-layout,
        reserved-and-limitations §7); add a pointer from ADR-007 §11.3/§26.
```

Steps follow the repository rule "classify before acting": 12.3 produces only
reports, so a classification bug cannot cause an unwanted submission.

---

## 9. Open items and DEFERRED work

Blockers for the claims they limit (none blocks committing this ADR):

```text
B1  Invocation and capture supervision. Scheduling is decided (§3.2: stateless one-shot,
    compose polling loop locally, deployment-level scheduler later, no dependence on
    Celery Beat/Redis). Still open: the router has no entrypoint and nothing supervises or
    restarts one-shot capture, so recovery of W1/W12 remains "re-run capture" until a
    capture supervisor exists. Phase 12 cannot claim unattended capture recovery.
B2  Kafka retention is not configured in the repository; recovery of W1/W12 assumes
    retention exceeds recovery time. Must be set and measured, not assumed.
B3  ArtifactStore Protocol change touches every implementer and test double
    (e.g. CountingArtifactStore).
B4  Stall threshold. RESOLVED in Amendment 12.4: 900 s default, configurable, from measured
    registration latency. `heartbeat_at` is still not refreshed during a job and
    registration still reads the whole recording into memory, so the value is only as good
    as the measurement (one host, local storage).
B5  Redis durability of the Celery queue is unverified; W7 resolution does not depend
    on it, but its frequency does.
B6  RESOLVED in Amendment 12.4: a stalled Job is abandoned and replaced by a forced Job;
    re-dispatching the same Job is not used.
B7  Router convergence (F6) and a capture-volume lease (F8) matter only if the router
    or concurrent capture processes are productionized.
B8  Batch acquisition (`dataset-acquisition`) has no receipt producer; it keeps its
    synchronous publish-then-register script and relies on publisher/registrar
    idempotency plus the reconciler. A receipt from that tool is optional.
B9  Single-request S3 `put_object` limits recording size to 5 GB; larger recordings
    need multipart upload. Not measured; out of scope.
```

State at closure (Amendment 12.6): B3, B4 and B6 are resolved; B1, B2, B5, B7, B8 and
B9 remain as the limits listed there.

DEFERRED, with triggers for revisiting:

```text
CaptureSessionRecord / AcquisitionRecord in PostgreSQL
    revisit when: store listing is measured too slow for operator queries, failure
    history must outlive Job rows, or multiple publishers per run_id are required.
Unified capture→register status view (capture volume visible to the platform)
Conditional writes (If-None-Match) for write-once keys (ADR-007 §26)
Router productionization, capture lease, router "same content → converge"
Artifact deletion / garbage collection / quarantine (§6.3)
Prometheus / Grafana / OpenTelemetry
Receipt for batch-acquired recordings
```

## 10. Consequences

**Positive.** Every failure window of §2 ends in a classified, resumable or
explicitly-failed state using facts that already exist. Capture gains no
dependency. No migration. The manifest, `RobotRunRecord` and registration
contract are untouched. The riskiest later operation (deletion) is separated
from classification and gated on a protected set (PN-2).

**Costs.** One new file format (receipt), one new port method, one new
platform component (reconciler), whose invocation is a deployment concern (one-shot
contract plus a compose polling loop locally).
Registration visibility for a finalized-but-unpublished recording remains
split across two read-only reports. Recovery from W1/W13 still depends on
Kafka retention. `RobotRun` provenance is unchanged, so an interrupted
(truncated) capture finalized by idle timeout remains indistinguishable in the
manifest; only the receipt records why it ended.
