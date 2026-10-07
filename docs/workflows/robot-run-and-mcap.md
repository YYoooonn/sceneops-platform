# Robot Data Acquisition: batch or streaming -> L1 MCAP -> RobotRun

A robot's data enters SceneOps as an L1 raw recording (an MCAP, ADR-007
§29.5) acquired in one of two modes -- batch (an external dataset converted
by `tools/dataset-acquisition`) or streaming (ROS2 topics -> bridge -> Kafka
-> capture) -- and is registered as a `RobotRun`. An adapter decodes the
recording (real CDR encoding, not a mock) into `RobotState`/`Mission` rows,
and the recording builders produce canonical Scenes and Episodes. This doc
covers what's actually implemented, followed by current, verified
limitations.

## 1. End-to-end flow

```text
batch      external dataset -> tools/dataset-acquisition -> MCAP
streaming  robot / dataset replay -> ROS2 topics -> streaming_bridge_node -> Kafka
             -> ros2/capture -> MCAP                      (docs/architecture/streaming-transport.md)

either     -> L1 conformance check -> Recording Publisher (no DB) -> MCAP + RobotRunManifest in Object Storage
           -> REGISTER_ROBOT_RUN (POST /robot-runs:register, or submitted by reconcile --apply) -> RobotRun (§3.2)
           -> resolve_recording(robot_run_id) -- verified local copy (§3.1)
           +-> RecordingTelemetryReader (apps/worker) -- derived telemetry projection
           |     -> ingest_robot_states Job -> Postgres (RobotState, Mission)
           |          -> export_robot_analytics_snapshot Job -> Parquet (Artifact Store)
           |               -> DuckDB query (sceneops_analytics.query_parquet)
           +-> recording reader -> build_recording_episodes -> register_episodes
           |     -> validate_episode / profile_episode
           |     (RECORDING_EPISODE_BUILDING pipeline -> EpisodeRecord)
           +-> recording reader -> build_recording_scenes -> ... (RECORDING_SCENE_BUILDING)
```

A recording replayed through the streaming path yields a semantically equivalent
recording and, with source-timestamp build configurations, equivalent canonical
Scenes and Episodes (ADR-007 §29.12, §32; `make e2e-streaming-equivalence`
compares the reference contract's streamed RobotRun of a locked MCAP with the
imported one, read-only).

The telemetry projection and canonical Episode / Scene building read the
same resolved recording independently. Canonical Episodes never read the
telemetry tables: their streams, fields, clocks and segmentation come from
the Episode build configuration (see
[Episode domain](../architecture/episode-domain.md)).

## 2. Telemetry topics

The vehicle telemetry channels the batch tool and the streaming path both carry:

```text
/vehicle/odom      (nav_msgs/Odometry)       <- CAN 'pose'             -> position, orientation, velocity
/vehicle/imu       (sensor_msgs/Imu)          <- CAN 'ms_imu'           -> orientation, acceleration
/vehicle/control   (std_msgs/String, JSON)    <- CAN 'vehicle_monitor'  -> steering, throttle, brake
/vehicle/status    (sensor_msgs/BatteryState) <- CAN 'vehicle_monitor'  -> battery
/mission/status    (std_msgs/String, JSON)    <- synthetic start/end events on the source timeline -> Mission, not RobotState
```

Standard ROS2 messages (`nav_msgs`, `sensor_msgs`) are used wherever they
fit. `/vehicle/control` has no matching standard message for a
steering+throttle+brake tuple, and a custom `.msg` package would need a
`colcon` build step — out of scope for the acquisition tool — so it's carried as
flat JSON inside `std_msgs/String`, which `RecordingTelemetryReader` recognizes and
unwraps; the Episode builder reads it with `decoding: json_string`.

`/mission/status`'s `operation_state` values (`"running"`/`"completed"`)
are `MissionStatus` values, not `RobotOperationState`
(idle/running/error/emergency_stop) values — feeding them into
`RobotStateRecord` directly would fail Pydantic validation. `RecordingTelemetryReader`
excludes `/mission/status` from `extract_robot_states()` for exactly this
reason and routes it through `extract_missions()` instead, which merges
every status update sharing a `mission_id` into one `MissionRecord` and
maps the string to `MissionStatus` (unrecognized values fall back to
`PENDING`).

nuScenes CAN quaternions are `(w, x, y, z)`; ROS2 `geometry_msgs/Quaternion`
is `(x, y, z, w)` — the acquisition tool's nuScenes adapter handles the
reorder.

## 3. Reading a recording

Two readers consume a resolved recording (§3.1):

- `RecordingTelemetryReader`
  (`apps/worker/sceneops_worker/robots/telemetry.py`) is the
  robot-telemetry projection read: `extract_robot_states()`,
  `extract_missions()`. It decodes `cdr` messages
  with `mcap_ros2` using the schemas embedded in the file (no rclpy),
  flattens `nav_msgs/Odometry`, `sensor_msgs/Imu` and
  `sensor_msgs/BatteryState` into robot-state fields, and re-parses
  `std_msgs/String` `.data` as JSON (the §2 bridge format).
- `sceneops_integrations.recording.reader` is the reader canonical Scene
  building uses. It streams every message in file order with its topic,
  schema, encodings, payload, `log_time`, `publish_time`, MCAP sequence and
  file position, and decodes ROS 2 messages with the embedded schema. The L1
  conformance suite shares its timestamp helpers. See
  [Scene domain](../architecture/scene-domain.md) §6.

Storage: external inputs follow `InputSourceSettings`' independent-root
convention (`/data/inputs/...`); a published recording lives under
`{artifact root}/robot_runs/{run_id}/` (see
[Storage layout](../architecture/storage-layout.md) §3).

### 3.1 Recording consumption: the verified recording resolver

`RecordingTelemetryReader` stays storage-agnostic — it only ever opens a local
filesystem path (`mcap.reader.make_reader(open(path, "rb"))`), never
`s3://`, MinIO, `ArtifactStore`, or HTTP directly.

Every job that reads a registered recording identifies it by
`robot_run_id` only and obtains it through one resolver,
`resolve_recording()` (`sceneops_worker.robots.resolver`, ADR-007 §12.4):

```text
robot_run_id
  -> RobotRunRecord                       missing -> RobotRunNotFoundError
  -> recording ArtifactRecord             missing, wrong kind, or no
     (RobotRunRecord.recording_artifact_id)  checksum/size -> inconsistent
                                          canonical state, fails
  -> ArtifactStore.read_bytes(uri)        any backend (LocalArtifactStore,
                                          MinIO/S3); bytes absent -> fails
  -> execution-scoped local copy          fresh tempfile.TemporaryDirectory
  -> verify size, then sha256, of the     mismatch -> RecordingIntegrityError
     local copy against the ArtifactRecord
  -> VerifiedRecording(robot_run_id, robot_id, local_path, recording_format,
                       source_clock, artifact_id, checksum, size_bytes)
  -> RecordingTelemetryReader(recording_path=local_path)
```

Consumers: `build_recording_scenes`, `build_recording_episodes` and `ingest_robot_states`. Their job params take
`robot_run_id` (required); `mcap_uri`, `rosbag_uri` and `robot_id` are
rejected at job creation, and there is no local-path parameter and no
fallback to any other recording source. The RobotRunRecord's `robot_id` is
authoritative for the robot that produced the recording: Episodes,
RobotStates and Missions derived from it carry that robot, and a caller
cannot relabel the recording as another robot's. A recording is never read unverified, and the
registered ArtifactRecord's size and checksum are the only integrity
reference — not a backend ETag, a filename, or a URI. Only the `mcap`
recording format is supported.

**Lifecycle.** The resolver owns the local copy; the consumer borrows its
path (read-only) for the duration of the `async with resolve_recording(...)`
block. The copy is deleted when the block exits — after normal completion,
a consumer or reader exception, or a verification failure. The local
backend is copied like any other, so a consumer never holds a path to the
stored artifact itself. Concurrent resolutions of the same RobotRun get
independent copies; there is no shared cache. A killed process (`SIGKILL`,
container death) can leave a copy behind with no `finally` having run;
it is disposable temp data that nothing else reads, reclaimed with the
container's ephemeral filesystem.

**Read-only.** The resolver never writes RobotRunRecords, ArtifactRecords,
or stored recording bytes. Retrying a consumer resolves and verifies the
recording again.

### 3.2 Publication and registration

One acquisition passes through four stages. Each is defined by a durable fact,
owned by one component, and resumed by one stateless command; no status is
stored anywhere ([ADR-008](../adr/008-acquisition-lifecycle-reliability.md)):

```text
stage         durable fact                                             owner      resumed by
------------  -------------------------------------------------------  ---------  ----------------------------------
capture       <capture root>/<run_id>/ holding the MCAP and            Capture    re-running capture while Kafka
              capture_receipt.json (one atomic rename from .partial/)             still retains the run's records
publication   {robot_run_root}/{run_id}/recording.mcap, then           Publisher  publish-pending
              robot_run_manifest.json (the marker, written last)
registration  RobotRunRecord + its two ArtifactRecords                 Worker     reconcile --once --apply
              (one transaction of a REGISTER_ROBOT_RUN Job)
```

A finalized capture therefore reaches a RobotRun with no operator input: the
receipt carries the robot, platform, capture source and clock that publication
needs, `publish-pending` publishes it, and `reconcile --apply` submits and, if
need be, retries or replaces the registration. The explicit commands below are
the same code paths run by hand, and the only paths for a recording that has no
receipt (batch acquisition, a robot's own recorder).

A RobotRun exists only for a recording that was published and verified
(ADR-007 §7, §12):

```text
finalized local MCAP (a capture directory, or the acquisition tool's output)
  -> python -m sceneops_integrations.recording publish     (DB-free, own process)
       --from-capture <dir>   every input comes from capture_receipt.json
       --mcap-path ... --robot-id ... --source-kind ...   explicit inputs, for a recording without a receipt
       P1 validate MCAP, derive facts (time range, channels, counts), sha256 + size
       P3 {robot_run_root}/{run_id}/recording.mcap            write-once, re-read + verified
       P5 {robot_run_root}/{run_id}/robot_run_manifest.json   canonical RobotRunManifest v1, LAST
  -> reconcile --once --apply submits REGISTER_ROBOT_RUN for every manifest with no RobotRun,
     or by hand: POST /robot-runs:register {"manifest_uri": ...}   (202, REGISTER_ROBOT_RUN Job)
     or sceneops-worker robots register --manifest-uri ...  (same registrar, in-process)
       verify manifest (strict, byte-canonical) + recording (exists, size, sha256, MCAP facts)
       one transaction: Robot create / platform fill-once,
                        ArtifactRecord(robot_run_recording), ArtifactRecord(robot_run_manifest),
                        RobotRunRecord (immutable)
```

The manifest is the publication marker: a crash before it is written
leaves no manifest, and a retry reuses the already-uploaded recording.
Republishing identical inputs writes nothing; an existing key with
different bytes is a hard conflict and is never overwritten. Running the
registrar again for the same manifest is a no-op that reports
`created=false`; a different manifest for a registered `run_id` fails, and
so does a manifest `robot_platform` that contradicts the Robot's set
platform. Registration never uploads, moves or rewrites bytes.

`created` describes the registrar execution that produced a Job result,
not the HTTP request that returned it. `POST /robot-runs:register` goes
through Job execution-key dedup (see
[Jobs and pipelines](../architecture/jobs-and-pipelines.md)): an identical
`manifest_uri` returns the existing pending, running or succeeded Job with
`execution: null`, so a retry after success sees that Job's original
result (`created=true`) and no second registration runs. The registrar
runs again, and reports `created=false`, only through a new Job
(`force: true` on `POST /jobs`) or `sceneops-worker robots register`.

`robot_run_root` defaults to the ArtifactSettings' `robot_run_root_uri`
(`{artifact root}/robot_runs`); the publisher reads its ArtifactStore
settings from `SCENEOPS_PUBLISHER_ARTIFACT__*`.

Write-once enforcement is "check, write, re-read" on an ArtifactStore
without conditional create. Two publishers racing on one `run_id` with
different bytes are detected (post-write verification, registration and
consumer checksums) rather than prevented; capture routing by
`robot_run_id` provides the single-publisher assumption.

**Observing and recovering acquisition state.** Stateless one-shot commands
report where every `run_id` is in the lifecycle and, when asked, act on the
states that are safe to act on. Each re-derives everything from durable facts,
keeps nothing between invocations and is safe at any frequency, concurrently and
after any crash:

```text
python -m sceneops_integrations.recording scan-capture --capture-root <capture output root>
    -> JSON CaptureScanReport (DB-free, read-only; reads directory entries, sizes and receipts, never recording bytes)
python -m sceneops_integrations.recording publish-pending --capture-root <capture output root>
    -> JSON PublishPendingReport (DB-free; publishes finalized captures that have a receipt and are not completely published)
python -m app.domains.robots.reconciliation --once [--capture-report <scan-capture JSON | ->]
    -> JSON ReconciliationReport, read-only (from apps/api; needs ArtifactStore + PostgreSQL, not the HTTP server)
python -m app.domains.robots.reconciliation --once --apply
    -> the same report, after bounded registration recovery
```

The reconciler lists `robot_run_root` and reads each manifest object, reads
RobotRunRecords, the two RobotRun ArtifactRecords and every
`REGISTER_ROBOT_RUN` Job with the manifest's execution key from PostgreSQL
(one `READ ONLY` transaction), and compares the recording bytes of runs that
are published but not registered against their manifest. The platform never
mounts the capture volume: capture facts arrive only as the `scan-capture`
report. One state per `run_id`, derived only from durable facts:

| State | Facts |
| --- | --- |
| `capture_unfinished` | `.partial/<run_id>/` exists, no finalized bag |
| `finalized_no_receipt` | finalized bag without a capture receipt, nothing published |
| `publish_pending` | finalized bag with a valid receipt, nothing published |
| `publication_incomplete` | recording without a valid manifest, manifest without its recording, or a malformed manifest |
| `registration_pending` | valid manifest and recording, no RobotRunRecord, no `REGISTER_ROBOT_RUN` Job in flight |
| `registration_active` | a Job is `pending`, `queued` or `running` |
| `registration_stalled_candidate` | every in-flight Job has shown no activity for longer than the stall threshold |
| `registration_failed_transient` / `registration_failed_permanent` | the newest Job failed; classified by its recorded exception class. A transient failure becomes permanent (reason `attempt_budget_exhausted`) once the logical registration has spent its attempts |
| `registered` | RobotRunRecord exists with the manifest's `manifest_checksum`; any Job state is ignored |
| `permanent_conflict` | RobotRunRecord exists with a different `manifest_checksum` |
| `integrity_incident` | the facts contradict each other: recording size or checksum differs from its manifest, ArtifactRecords without a RobotRunRecord, a registered run whose objects or ArtifactRecords disagree, an unusable capture receipt |

A Job's success never makes a run `registered`. Observation is read-only and
the report holds no wall-clock reading; its states are always what was observed
before any action. Exit status is 0 whenever a report was produced.

**Recovery actions.** `publish-pending` and `reconcile --apply` are the only
actors, and each acts on a short list of states:

| State | Action |
| --- | --- |
| finalized capture, valid receipt, nothing in the store (`publish_pending`) | `publish-pending` publishes it through the `publish --from-capture` path |
| recording uploaded, manifest missing, receipt present (`publication_incomplete`) | `publish-pending` reuses the recording and writes the manifest |
| `registration_pending` with no Job | submit `REGISTER_ROBOT_RUN` |
| `registration_failed_transient`, attempts remain | submit again |
| `registration_stalled_candidate`, attempts remain | mark the stalled Job `FAILED` / `JobAbandoned`, then submit a forced replacement Job |
| `registered` | nothing, whatever a Job row says |
| `permanent_conflict`, `integrity_incident`, `registration_failed_permanent` (including a spent budget), `registration_active`, a manifest that contradicts its recording, a capture without a receipt | nothing; an operator decides |

All registration goes through the same Job and dispatch path as
`POST /robot-runs:register`; the worker handler is unchanged and the reconciler
writes no object, RobotRunRecord or ArtifactRecord. `publish-pending` never
overwrites an object: a conflicting existing one is reported as `failed` and
every other capture is still processed.

A logical registration is its execution key, `REGISTER_ROBOT_RUN(manifest_uri)`.
Every `FAILED` Job of that key, abandoned ones included, is one attempt out of
three; replacement Jobs share the count and a new Job row never resets it.
Success ends recovery. An operator's forced submission is outside the budget.
Each run's report entry shows `failed_job_count`, `abandoned_job_count`,
`attempt_budget` and `attempts_remaining`, and `--apply` adds an `actions` list
(what was submitted, abandoned, skipped or failed, with the Job ids). One pass
performs at most 100 actions; the rest wait for the next pass.

The stall threshold (`--stall-threshold-seconds`, or
`SCENEOPS_API_RECONCILER__STALL_THRESHOLD_SECONDS`) is 900 s by default
([ADR-008](../adr/008-acquisition-lifecycle-reliability.md) Amendment 12.4 gives
the measurements it comes from). If the broker refuses a dispatch the Job stays
committed as `pending` / `queued` and is recovered as a stalled Job once it has
been inactive for the threshold; classification never needs Redis.

Locally, `make recovery-up` starts two loops (`compose/recovery.yaml`) that only
repeat `publish-pending` and `reconcile --once --apply` every
`RECOVERY_POLL_INTERVAL_SECONDS` (default 60); `make recovery-logs` follows them,
and `make reconcile-once` and `make reconcile-apply` run one pass by hand. A
Kubernetes deployment would run the same two commands from CronJobs. The platform
has no scheduler (no Celery Beat) and recovery never depends on one.

**Recovery logs.** Every action `publish-pending` or `reconcile --apply` takes is
one line on stderr, `acquisition_recovery {json}`, and every pass ends with one
`acquisition_recovery_pass {json}` line; stdout stays the one JSON report. A
record carries what was observed before and after, what was attempted and what
happened:

```text
component      publish-pending | reconcile
run_id, stage  publish | register
action         publish | submit_registration | retry_registration | replace_stalled_job | abandon_stalled_job | none
outcome        published | failed | skipped | submitted | deduplicated | dispatch_failed | abandoned | lost_race | error
state_before   the run's state when the pass observed it (publish_pending, registration_stalled_candidate, ...)
state_after    the state observed again after the pass's actions (absent for a skip, or if that observation failed)
job_id, abandoned_job_ids, reason, error_type, error
failure_class, attempts_used, attempt_budget, attempts_remaining      (registration actions)
duration_ms, ts
```

`publish-pending` logs publication attempts only (a capture that stays
unpublishable would otherwise add a line to every pass); its pass line counts
what it skipped by reason. The lines are a record of what a command did, not a
source of truth: nothing reads them back and every decision is recomputed from
durable facts.

**Artifact lifecycle of `robot_runs/`.** A read-only one-shot command classifies
every object under the RobotRun root, and every database reference to one, by
whether deleting it could ever be safe. It deletes, moves and repairs nothing; a
class is a classification, not an instruction.

```text
python -m app.domains.robots.artifact_lifecycle --once \
    [--capture-report <scan-capture JSON | ->] [--observed-at <ISO-8601 with offset>] \
    [--pending-grace-seconds N] [--orphan-grace-seconds N] [--verify-recording-bytes]
    -> JSON ArtifactLifecycleReport (needs ArtifactStore + PostgreSQL, not the HTTP server)
```

It builds on the reconciler's facts (the same listing, RobotRunRecords, Jobs and
per-run states) and adds the reference query: an object is *referenced* iff some
ArtifactRecord carries exactly its URI; ownership is never inferred from the
object name.

| Class | Meaning |
| --- | --- |
| `referenced` | an ArtifactRecord references the URI and nothing contradicts it |
| `pending` | unreferenced but protected: younger than the pending grace (24 h by default), the run's registration is unfinished (`registration_pending`, `registration_active`, `registration_stalled_candidate`, `registration_failed_transient`; any age), a capture bag or receipt still exists, or older than the pending grace but not yet the orphan grace (7 d by default) |
| `orphan_candidate` | unreferenced, unprotected and older than the orphan grace. `recording_without_manifest`: a recording with no manifest and no capture source (the capture report must show that). `recording_manifest_permanently_failed`: a consistent pair whose registration failed permanently, a spent attempt budget included. Both are high risk: they may be the only copy of a recording |
| `integrity_incident` | the durable facts contradict each other, at any age: an ArtifactRecord whose object is absent (`dangling_reference`); a record whose size or known checksum disagrees with the object (`referenced_size_mismatch`, `referenced_corrupt`); a RobotRunRecord whose objects are gone; a run whose reconciliation state is `integrity_incident` or `permanent_conflict`; a malformed manifest or a manifest without its recording. Never a candidate |

The age of an unreferenced publication is that of the newest of its two objects.
Without `--capture-report` the capture volume is unobservable, so a recording
without a manifest stays `pending` (`pn3_capture_source_unobserved`) and is never
an orphan candidate. Registered recordings are compared to their records by
listing size; `--verify-recording-bytes` re-reads and hashes them. Objects that
match no known layout, and are unreferenced, are listed under `unclassified` and
are never candidates.

The report contains `observed_at`, which every age and grace period is measured
against (default: now; `--observed-at` fixes it), so the same facts and the same
`observed_at` give a byte-identical report. Its `summary` carries the referenced,
pending (with the oldest age), orphan-candidate (by reason and risk) and
incident counts and bytes. A finding whose object appears while the scan runs is
dropped and counted in `unconfirmed_findings`. A classification can be stale by
the time anyone acts on it; any future deletion must re-verify. Grace periods are
also set by `SCENEOPS_API_ARTIFACT_LIFECYCLE__PENDING_GRACE_SECONDS` and
`..._ORPHAN_GRACE_SECONDS`; `make artifact-lifecycle-once` runs one pass.

**Acquisition status and operational report.** A read-only one-shot command
derives an `AcquisitionStatus` for every run and aggregates them. Nothing is
stored: there is no status table and no lifecycle column, and the view adds no
lifecycle semantics of its own. It is a function of the reconciler's per-run
state, the artifact lifecycle entries and the observation time.

```text
python -m app.domains.robots.acquisition_status --once \
    [--summary-only] [--capture-report <scan-capture JSON | ->] [--observed-at <ISO-8601 with offset>] \
    [--stall-threshold-seconds N] [--pending-grace-seconds N] [--orphan-grace-seconds N] \
    [--verify-recording-bytes]
    -> JSON AcquisitionOperationalReport (needs ArtifactStore + PostgreSQL, not the HTTP server, not Redis)
```

Per run (`runs`, omitted by `--summary-only`):

| Field | Meaning |
| --- | --- |
| `stage` | the furthest durable fact: `capture_unfinished`, `finalized`, `published`, `registered` |
| `health` | `ok`, `pending`, `stalled`, `failed` or `inconsistent` (table below) |
| `classification`, `reasons` | the reconciler's state for the run and the codes behind it |
| `operator_required` | nothing automatic will move the run on and a person must decide |
| `failure` | the unresolved registration failure: class, exception type, attempts, budget and attempts remaining. Absent for a registered run, whose failed Jobs are history |
| `capture`, `publication`, `registration` | the facts of each stage: receipt claims (needs a capture report), manifest and object facts, RobotRun and Job counts, abandoned Jobs, the latest Job |
| `timestamps`, `durations`, `age_seconds` | `finalized_at`, `published_at`, `registered_at`, the newest durable activity, the seconds between stages, and the time since that activity |
| `recording_bytes`, `message_count`, `channel_count`, `finalization_reason` | from the manifest, else the receipt; absent when neither was observed |
| `artifacts`, `findings` | the run's lifecycle entries: referenced, pending, orphan-candidate objects and incident entries, with bytes; every entry that is not simply `referenced` |

| State | `health` | `operator_required` |
| --- | --- | --- |
| `registered` | `ok` | no |
| `capture_unfinished`, `publish_pending`, `registration_active`, `registration_failed_transient`, `registration_pending` with no Job | `pending` | no |
| `publication_incomplete`: a recording only, resumable from a capture | `pending` | no |
| `publication_incomplete`: a recording only, no capture source; `registration_pending` whose newest Job was cancelled; `finalized_no_receipt` | `pending` | yes |
| `registration_stalled_candidate` | `stalled` | only when the attempt budget is spent |
| `registration_failed_permanent` (a spent budget included) | `failed` | yes |
| `permanent_conflict`, `integrity_incident`, `publication_incomplete` with a malformed manifest or a manifest without its recording, `registration_pending` after a success with no RobotRun | `inconsistent` | yes |

A lifecycle entry that is an integrity incident raises any other health to
`inconsistent`. `finalized_at` is the capture host's clock, `published_at` the
object store's and `registered_at` PostgreSQL's, so a duration between two of
them is indicative across hosts and is reported as observed. A failed publish is
the Publisher's report (`publish-pending` prints and logs it), not a durable
fact, so a capture whose publication keeps failing stays `publish_pending` and
its age grows.

The aggregates, always computed over every run:

| Aggregate | Content |
| --- | --- |
| `by_stage`, `by_health`, `by_classification`, `operator_required` | run counts |
| `registration` | runs pending, active, stalled, failed transient / permanent; runs whose attempt budget is spent; attempts used (a histogram) and abandoned Jobs of the runs not yet registered |
| `oldest` | for each non-terminal state, the run whose newest durable activity is oldest, with its age. It is time since last activity, not a judgement that the run is lost |
| `incidents`, `artifacts`, `unconfirmed_findings` | runs in `integrity_incident` / `permanent_conflict` by reason; referenced, pending, orphan-candidate and incident objects and bytes |
| `recordings` | runs with a known size and message count, and their totals |
| `attention` | every run that is stalled, failed, inconsistent or needs an operator |

`observed_at` is part of the report (`--observed-at` fixes it) and ages, the stall
threshold and the grace periods are measured against it, so the same facts and
the same `observed_at` give a byte-identical report. One `acquisition_status
{json}` line with the aggregates (no per-run statuses) is also logged to stderr.
Without `--capture-report` the capture stages are unobservable: a run that is
still capturing, or finalized but unpublished, does not appear. `make
acquisition-status` runs one pass; the recovery loops do not run it.

**Limits of the operational model.**

- Nothing supervises capture. A capture killed before finalize is re-run by an
  operator, and only while Kafka still retains the run's records; broker
  retention is not configured by the repository and recovery assumes it exceeds
  the time to notice. `capture_unfinished` is reported with its age; the platform
  cannot tell an interrupted capture from one still running.
- The stall threshold (900 s) was measured on one host with local storage;
  deployments with slower storage or longer queues must measure and raise it. A
  killed or lost registration is therefore recovered no sooner than the threshold
  plus one polling interval.
- The attempt budget is shared by every Job of a registration. A broker outage
  longer than the budget times the threshold (about 45 minutes at the defaults)
  can spend it without the registration ever having failed; an operator's forced
  submission then registers the run.
- Plain submissions (a first Job, a transient retry) are not serialized, so
  concurrent passes can create a duplicate Job. Registration converges on one
  RobotRun regardless.
- The artifact lifecycle covers `robot_runs/` only and classifies without
  deleting: no artifact is deleted, quarantined or repaired. A registered
  recording is checked by size unless `--verify-recording-bytes`.
- The capture volume is visible to the platform only as a `scan-capture` report;
  there is no joined capture-to-registration view without one.
- Everything is validated on one local host and stack. How it behaves at larger
  scale is not measured here.

### 3.3 Batch acquisition and the L1 recording contract

A recording that already exists — a robot's onboard recorder, or an
external dataset converted by the acquisition tool — enters through the
same publisher and registrar as a capture (ADR-007 §29.4). Only
`capture.source.kind` (`file`) differs, and nothing downstream branches on
it:

```text
external dataset (e.g. nuScenes v1.0-mini scene, read-only mount)
  -> dataset-acquisition container      tools/dataset-acquisition; no SceneOps package,
                                        no network, no credentials
       -> finalized MCAP                 camera, CameraInfo, lidar, /tf_static, /tf,
                                         CAN telemetry, mission events
                                         (acquisition-recordings volume)
  -> recording-publisher container      python -m sceneops_integrations.recording
       check                             L1 conformance (optional, read-only)
       publish --source-kind file        recording + RobotRunManifest -> ArtifactStore;
                                         JSON result incl. manifest_uri
  -> POST /robot-runs:register           REGISTER_ROBOT_RUN Job -> RobotRun
```

Both containers are one-shot compose services (`compose/acquisition.yaml`,
profile `acquisition`). They are data-plane steps outside the API.
`recording-publisher` runs the publisher from the worker image with
ArtifactStore settings only. It receives no database settings. Recording
bytes never pass through the API. Registration and everything after it go
through FastAPI. `make canonical-bootstrap` runs this path as developer
orchestration from the host with only Docker Compose, curl and jq.

The tool stops at the MCAP. It never publishes, registers or calls
SceneOps. Its channel mapping, timing policy and calibration representation
are documented in
[`tools/dataset-acquisition/README.md`](../../tools/dataset-acquisition/README.md).
The source unit it converts (a nuScenes scene name) selects input only. It
is not a SceneOps Scene, and the recording carries no Scene, Episode or
DatasetVersion information.

**L1 conformance.** `check_l1_recording()`
(`sceneops_integrations.recording.conformance`, CLI `... recording check
--mcap-path`) checks what the bytes of any L1 recording can prove: a
finalized MCAP, one channel definition per topic, embedded schemas with
the `ros2`/`cdr`/`ros2msg` profile, every payload decoding with its own
schema, `log_time` non-decreasing in write order, per-channel sequence
numbers increasing, camera / range-sensor frames connected by a transform
recorded at or before their first message, CameraInfo for every image
frame, and no SceneOps canonical identifiers in metadata or schemas. It
reports per-channel facts (counts, `log_time` and source-stamp ranges,
frame ids) and an optional `sceneops.acquisition_origin` metadata record,
which it never interprets. Source-time fidelity and event timelines are
writer obligations, checked by each writer's tests against its source
data. Capture output does not conform yet: it writes the source timestamp
into `log_time` (ADR-007 §29.20).

**Acquisition origin.** The tool writes `sceneops.acquisition_origin`
(tool, source format, version, unit) into the MCAP as an MCAP metadata
record. It is covered by the recording checksum. It is not part of the
RobotRunManifest, and publication, registration and the resolver never
read it.

## 4. Entity relationships

```text
Robot        robot_id, name, platform -- static metadata
RobotRun     run_id, robot_id -- one finalized recording; references its recording + manifest ArtifactRecords
Mission      mission_id, robot_id, status -- referenced by RobotState.mission_id
```

`RobotRun` is not the same concept as `PipelineRun` (see
[Data model](../architecture/data-model.md) §5) — `PipelineRun` is a
SceneOps-internal processing execution; `RobotRun` is a physical robot
execution (one published recording corresponds to one `RobotRun`).

`SceneRecord.parent_scene_id`/`lineage` (JSONB) is reused as-is to track
which `RobotRun` a scene came from — no new lineage mechanism was built for
robot data. The Scene-domain quality gate mechanism
(`PipelineTaskQualityRule`) is equally reusable for robotics-specific
validation (e.g. "does `CAM_FRONT`/`LIDAR_TOP` exist," "sensor timestamp
tolerance") by adding a new rule to `validate_scene`, without new
infrastructure.

## 5. Quickstart

Canonical Scenes and Episodes come from a batch-acquired recording through the
FastAPI control plane:

```bash
make local-up
make canonical-bootstrap             # prepared recordings -> RobotRun -> Scenes + Episodes (reusable L1/L2 baseline)
make e2e-episode-learning            # Episodes -> AlignedEpisodes -> learning export -> LeRobot round trip
```

The streaming vertical (locked reference MCAP -> replay -> ROS2 -> bridge -> Kafka
-> capture -> publish-pending -> reconcile -> RobotRun, then Scenes and Episodes
equivalent to the Recording Import baseline's; needs `make reference-data-bootstrap` once) is the
reference contract's Streaming Acquisition baseline, and its equivalence with Recording
Import is checked read-only:

```bash
make local-up
make reference-contract-bootstrap    # (once) both baselines; replays through ROS 2 -> Kafka -> capture
make e2e-streaming-equivalence       # reads the two registered RobotRuns of a fixture; creates nothing
```

Or fully manually (batch):

```bash
docker compose --profile acquisition run --rm dataset-acquisition nuscenes \
  --dataroot /input/nuscenes --source-unit scene-0061 --output /recordings/run-1.mcap

# Check + publish (DB-free) -- prints {"manifest_uri": ..., ...}:
docker compose --profile acquisition run --rm recording-publisher check --mcap-path /recordings/run-1.mcap
docker compose --profile acquisition run --rm recording-publisher publish \
  --mcap-path /recordings/run-1.mcap --run-id run-1 --robot-id robot-1 --source-kind file

# Register (REGISTER_ROBOT_RUN Job; poll GET /api/v1/jobs/{job_id}):
curl -X POST http://localhost:8000/api/v1/robot-runs:register \
  -H 'Content-Type: application/json' -d '{"manifest_uri": "<manifest_uri>"}'

# then dispatch `ingest_robot_states` (robot_run_id only) via
# POST /api/v1/jobs, or a recording_episode_building PipelineRun with the same
# robot_run_id and an Episode build_config, for episodes
```

Requires nuScenes v1.0-mini with the CAN bus expansion at
`data/raw/nuscenes/` (a separate download from nuScenes mini).

## 6. Current limitations

- **Sensor channels need their channel set.** Both acquisition modes carry
  camera, lidar, CameraInfo and transform channels; the streaming bridge and
  capture subscribe to the channels of the registry plus any `--channels-file`
  given to both (streaming-transport §11).
- **Publication and registration are explicit steps.** Nothing triggers
  the Recording Publisher from a finalized capture, or registration from a
  published manifest; published-but-unregistered manifests are not
  discovered automatically.
- **Recordings are read whole into memory** by the publisher, registration
  and the recording resolver (`ArtifactStore.read_bytes`), and the resolver
  then writes a full local copy before verification.
- **`/vehicle/control` uses a JSON bridge, not a real `.msg` package** — a
  deliberate scope cut to avoid a `colcon` build step; revisit if real
  robot integration needs a first-class message type.
- **No live robot control.** This is batch ingestion of a recording
  (replay -> record -> decode -> ingest), not real-time command/control —
  see [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) for the intended
  boundary once/if a streaming path is built.
- **Configure from source timestamps for acquisition-independent results.**
  A recording's `log_time` is the recorder's receive time: simulated source
  time in a batch recording, wall-clock capture time in a streamed one. A
  build configured from `mcap_log_time` depends on the acquisition; builds
  configured from header stamps or payload fields do not (ADR-007 §29.12,
  I-35).
- **Committed test fixture is real data**, not hand-crafted bytes:
  `apps/worker/tests/fixtures/rosbag/can_replay_scene_0061.mcap` (1.4MB)
  was produced by an actual `ros2 bag record` run of the CAN replay that
  the acquisition tool's replay sink has since replaced. The fixture is
  kept as a real-data test input.

## 7. Example output — scene-0061 CAN telemetry projection

```text
=== ingest_robot_states job result ===
robot_id       : robot-nuscenes-01
robot_run_id   : run-scene-0061
state_count    : 2913
mission_count  : 1

=== GET /missions?robot_run_id=run-scene-0061 ===
mission_id : mission-scene-0061
status     : completed

=== export_robot_analytics_snapshot job result ===
table_uris.robot_telemetry : s3://sceneops/artifacts/analytical/robot_runs/run-scene-0061/robot_telemetry.parquet
table_uris.missions         : s3://sceneops/artifacts/analytical/robot_runs/run-scene-0061/missions.parquet
row_counts                  : {robot_telemetry: 2913, missions: 1}
```

`state_count` is one row per CAN message (`pose`->odom, `ms_imu`->imu,
`vehicle_monitor`->status+control), not one row per mission — a single
`RobotRun` accumulates many `RobotState` rows.
