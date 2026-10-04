# Robot Data Ingestion: ROS2 -> CAN Replay -> rosbag2/MCAP -> RobotRun

A real ROS2 (Jazzy) node replays nuScenes CAN bus data as ROS2 topics,
records it with `rosbag2`/MCAP, and an adapter decodes the resulting bag
(real CDR encoding, not a mock) into `RobotState`/`Mission` rows and, via a
separate pipeline, `EpisodeRecord`s. This doc covers what's actually
implemented, followed by current, verified limitations — see
[Robot data model — design background](#8-design-background) at the bottom
for why the implementation gap turned out smaller than originally planned.

## 1. End-to-end flow

```text
nuScenes CAN bus data
  -> CanReplayNode (real rclpy, ros2/ Docker sandbox)
       -> ROS2 topics -- /vehicle/odom, /vehicle/imu, /vehicle/status, /vehicle/control, /mission/status
            -> ros2 bag record --storage mcap
                 -> rosbag2/MCAP file
                      -> Recording Publisher (no DB) -> MCAP + RobotRunManifest in Object Storage
                      -> POST /robot-runs:register -> REGISTER_ROBOT_RUN -> RobotRun (§3.2)
                      -> resolve_recording(robot_run_id) -- verified local copy (§3.1)
                      -> RosbagAdapter (apps/worker) -- decodes real CDR messages, no rclpy needed to read
                           +-> ingest_robot_states Job -> Postgres (RobotState, Mission)
                           |     -> export_robot_analytics_snapshot Job -> Parquet (Artifact Store)
                           |          -> DuckDB query (sceneops_analytics.query_parquet)
                           +-> build_episodes -> register_episode -> validate_episode -> profile_episode
                                 (RAW_LOG_EPISODE_BUILDING pipeline -> EpisodeRecord)
```

`ingest_robot_states` and Episode building both consume the same
`RosbagAdapter` decode of a bag, but through separate reads
(`RosbagAdapter.extract_robot_states()`/`extract_missions()` for the
former, `EpisodeSource` for the latter — see
[Episode domain](../architecture/episode-domain.md) §2) and can run
independently of each other.

## 2. ROS2 topics

```text
/vehicle/odom      (nav_msgs/Odometry)       <- CAN 'pose'             -> position, orientation, velocity
/vehicle/imu       (sensor_msgs/Imu)          <- CAN 'ms_imu'           -> orientation, acceleration
/vehicle/control   (std_msgs/String, JSON)    <- CAN 'vehicle_monitor'  -> steering, throttle, brake
/vehicle/status    (sensor_msgs/BatteryState) <- CAN 'vehicle_monitor'  -> battery
/mission/status    (std_msgs/String, JSON)    <- synthetic (replay start/end) -> Mission, not RobotState
```

Standard ROS2 messages (`nav_msgs`, `sensor_msgs`) are used wherever they
fit. `/vehicle/control` has no matching standard message for a
steering+throttle+brake tuple, and a custom `.msg` package would need a
`colcon` build step — out of scope for the replay node — so it's carried as
flat JSON inside `std_msgs/String`, which `RosbagAdapter` recognizes and
unwraps.

`/mission/status`'s `operation_state` values (`"running"`/`"completed"`)
are `MissionStatus` values, not `RobotOperationState`
(idle/running/error/emergency_stop) values — feeding them into
`RobotStateRecord` directly would fail Pydantic validation. `RosbagAdapter`
excludes `/mission/status` from `extract_robot_states()` for exactly this
reason and routes it through `extract_missions()` instead, which merges
every status update sharing a `mission_id` into one `MissionRecord` and
maps the string to `MissionStatus` (unrecognized values fall back to
`PENDING`).

nuScenes CAN quaternions are `(w, x, y, z)`; ROS2 `geometry_msgs/Quaternion`
is `(x, y, z, w)` — `can_replay_node.py`'s `_quat_wxyz_to_ros()` handles the
reorder.

## 3. Reading a recording

Two readers consume a resolved recording (§3.1):

- `RosbagAdapter`
  (`apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`) is the
  Episode / robot-state read: `extract_episode_source()`,
  `extract_robot_states()`, `extract_missions()`. It decodes `cdr` messages
  with `mcap_ros2` using the schemas embedded in the file (no rclpy),
  flattens `nav_msgs/Odometry`, `sensor_msgs/Imu` and
  `sensor_msgs/BatteryState` into robot-state fields, and re-parses
  `std_msgs/String` `.data` as JSON (the §2 bridge format). Sensor frames are
  named by their topic, verbatim.
- `sceneops_integrations.recording.reader` is the reader canonical Scene
  building uses. It streams every message in file order with its topic,
  schema, encodings, payload, `log_time`, `publish_time`, MCAP sequence and
  file position, and decodes ROS 2 messages with the embedded schema. The L1
  conformance suite shares its timestamp helpers. See
  [Scene domain](../architecture/scene-domain.md) §6.

Storage: locally recorded bags follow `RawSourceSettings`' independent-root
convention (`/data/raw/rosbag/...`); a published recording lives under
`{artifact root}/robot_runs/{run_id}/` (see
[Storage layout](../architecture/storage-layout.md) §3).

### 3.1 Recording consumption: the verified recording resolver

`RosbagAdapter` stays storage-agnostic — it only ever opens a local
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
  -> RosbagAdapter(local_path)
```

Consumers: `build_recording_scenes`, `build_episodes` and `ingest_robot_states`. Their job params take
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
finalized local MCAP (ros2 bag record, or ros2/capture's CaptureResult.path)
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
through FastAPI. `make e2e-batch-acquisition` exercises this path from the
host with only Docker Compose, curl and jq.

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

The primary path records the CAN replay and builds/curates the resulting
Episode(s) in one composed command:

```bash
make local-up
make e2e-robot-learning              # records CAN replay -> MCAP -> Episode -> alignment -> learning export -> curation
make e2e-robot-learning SCENE=scene-0061
make e2e-robot-learning MAX_SCENES=3  # first N CAN-bus-eligible nuScenes v1.0-mini scenes
```

Or stage by stage, for debugging one step in isolation (kept as debug/stage
targets, not the primary documented flow):

```bash
make ros2-up                        # ROS2 Jazzy sandbox (rclpy, rosbag2, MCAP storage plugin)
make e2e-robot-can-replay           # CAN replay -> record -> register -> ingest_robot_states (RobotState/Mission telemetry)
```

Or fully manually:

```bash
make ros2-can-replay-record SCENE=scene-0061 RATE=5.0

# Publish (DB-free) -- prints {"manifest_uri": ..., ...}. Inside worker-cli,
# map the worker's artifact settings to SCENEOPS_PUBLISHER_ARTIFACT__* (see
# scripts/e2e/lib.sh publish_robot_run_recording):
python -m sceneops_integrations.recording publish \
  --mcap-path /data/raw/rosbag/scene-0061/scene-0061_0.mcap \
  --run-id run-1 --robot-id robot-1 --robot-platform nuscenes-can-replay \
  --source-kind ros2_bag

# Register (REGISTER_ROBOT_RUN Job; poll GET /api/v1/jobs/{job_id}):
curl -X POST http://localhost:8000/api/v1/robot-runs:register \
  -H 'Content-Type: application/json' -d '{"manifest_uri": "<manifest_uri>"}'
# or: make worker-register-robot-run MANIFEST_URI=<manifest_uri>

# then dispatch `ingest_robot_states` (robot_run_id only) via
# POST /api/v1/jobs, or a raw_log_episode_building PipelineRun with the same
# robot_run_id, for episodes
```

Requires the nuScenes CAN bus expansion unzipped at
`data/raw/nuscenes/can_bus/` (a separate download from nuScenes mini).
Everything else is self-contained in the `ros2/` Docker image.

## 6. Current limitations

- **Sensor channels come only from batch acquisition.** Batch-acquired
  recordings (§3.3) carry camera, lidar, CameraInfo and transform channels,
  and `RECORDING_SCENE_BUILDING` extracts them into canonical Scene payloads.
  The Episode read (`RosbagAdapter`) leaves `RawSensorFrameManifest.uri`
  empty. The streaming path (bridge, capture) carries only the five
  telemetry channels of §2.
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
- **`mission_boundary` Episode segmentation is not supported for a
  Kafka-captured `RobotRun` — `BuildEpisodesJobHandler` rejects it with a
  clear `UnsupportedSegmentationError` rather than silently returning
  zero Episodes.** `/mission/status`'s timestamp is synthetic
  replay-event time (see
  [Streaming transport](../architecture/streaming-transport.md)'s
  MCAP-readiness table); a durably-captured MCAP preserves that value as
  `log_time` verbatim, while CAN-derived channels' `log_time` is real
  historical CAN observation time — the two never overlap, so
  `EpisodeBuilder`'s window-membership filter (§3 above) would otherwise
  keep zero frames for every Mission window and return an empty,
  misleading success. `BuildEpisodesJobHandler` detects exactly this
  outcome (Mission(s) present, `mission_boundary` requested, zero
  Episodes produced) and raises instead
  (`_reject_silent_zero_episode_mission_boundary`) — segmentation
  behavior itself is unchanged, this is a validation guard, not a
  redesign. The "no Missions at all" case still degrades to `whole_run`
  silently, exactly as before; only the "Missions exist but never
  overlap" case now fails loudly. A direct `ros2 bag record` capture
  never hits either path, because the recorder stamps every channel with
  its own receipt time uniformly, never threading `source_timestamp_ns`
  into `log_time` at all. **`whole_run` is the supported Episode
  segmentation strategy for a Kafka-captured `RobotRun`** (`build_episodes`'s
  `segmentation.strategy` param) — it needs no Mission/CAN timestamp
  alignment. There is no plan to introduce a separate replay/capture
  timeline representation for this (it would risk corrupting the CAN
  channels' real source-timestamp semantics for no clear benefit over
  just using `whole_run`).
- **Committed test fixture is real data**, not hand-crafted bytes:
  `apps/worker/tests/fixtures/rosbag/can_replay_scene_0061.mcap` (1.4MB)
  was produced by an actual `ros2 bag record` run. If CAN-replay logic
  changes, this fixture may need regenerating to keep expected values
  aligned.

## 7. Example output — scene-0061, 2875 CAN messages replayed at 10x

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
