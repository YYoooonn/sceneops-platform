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
           -> POST /robot-runs:register -> REGISTER_ROBOT_RUN -> RobotRun (§3.2)
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

The same source acquired either way yields semantically equivalent recordings
and, with source-timestamp build configurations, equivalent canonical Scenes
and Episodes (ADR-007 §29.12, §32; `make e2e-streaming-equivalence`).

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
convention (`/data/raw/...`); a published recording lives under
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

A RobotRun exists only for a recording that was published and verified
(ADR-007 §7, §12):

```text
finalized local MCAP (the acquisition tool's output, or ros2/capture's CaptureResult.path)
  -> python -m sceneops_integrations.recording publish     (DB-free, own process)
       P1 validate MCAP, derive facts (time range, channels, counts), sha256 + size
       P3 {robot_run_root}/{run_id}/recording.mcap            write-once, re-read + verified
       P5 {robot_run_root}/{run_id}/robot_run_manifest.json   canonical RobotRunManifest v1, LAST
  -> POST /robot-runs:register {"manifest_uri": ...}   (202, REGISTER_ROBOT_RUN Job)
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

**Observing acquisition state.** Two read-only one-shot commands report where
every `run_id` is in the lifecycle; neither writes an object, a row or a Job,
and neither acts on what it finds:

```text
python -m sceneops_integrations.recording scan-capture --capture-root <capture output root>
    -> JSON CaptureScanReport (DB-free; reads directory entries, sizes and receipts, never recording bytes)
python -m app.domains.robots.reconciliation --once [--capture-report <scan-capture JSON | ->]
    -> JSON ReconciliationReport (from apps/api; needs ArtifactStore + PostgreSQL, not the HTTP server)
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
| `registration_stalled_candidate` | every in-flight Job is older than a caller-supplied threshold (none is configured, so this is not reported by the command) |
| `registration_failed_transient` / `registration_failed_permanent` | the newest Job failed; classified by its recorded exception class |
| `registered` | RobotRunRecord exists with the manifest's `manifest_checksum`; any Job state is ignored |
| `permanent_conflict` | RobotRunRecord exists with a different `manifest_checksum` |
| `integrity_incident` | the facts contradict each other: recording size or checksum differs from its manifest, ArtifactRecords without a RobotRunRecord, a registered run whose objects or ArtifactRecords disagree, an unusable capture receipt |

A Job's success never makes a run `registered`. The report holds no
wall-clock reading, so reconciling an unchanged system twice yields identical
output. Exit status is 0 whenever a report was produced.

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
through FastAPI. `make e2e-batch-canonical` exercises this path from the
host with only Docker Compose, curl and jq, and `make canonical-bootstrap`
runs it as developer orchestration.

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
make e2e-batch-canonical             # nuScenes -> acquisition -> RobotRun -> Scenes + Episodes
make canonical-bootstrap             # the same path as a reusable L1/L2 baseline
make e2e-episode-learning            # Episodes -> AlignedEpisodes -> learning export -> LeRobot round trip
```

The streaming vertical (replay -> ROS2 -> bridge -> Kafka -> capture ->
RobotRun, then Scenes and Episodes equivalent to the batch acquisition):

```bash
make local-up
make streaming-up
make e2e-streaming-equivalence       # containers + FastAPI only; no host uv, PostgreSQL or MinIO access
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
