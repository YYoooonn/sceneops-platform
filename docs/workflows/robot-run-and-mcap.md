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

## 3. `RosbagAdapter`: bag -> SceneOps schemas

`apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`
implements the `RawLogAdapter` Protocol
(`apps/worker/sceneops_worker/observations/adapters/base.py`) — registered
under `RawLogSourceType.REAL_ROBOT_LOG` in `build_scenes.py`'s adapter
factory. nuScenes raw-log ingestion no longer goes through this same
in-process Protocol (it runs through the isolated nuScenes integration
service instead — see
[External integration runtime](../architecture/external-integration-runtime.md));
`RosbagAdapter` is the only remaining `RawLogAdapter` implementation.

```python
class RosbagAdapter:
    async def build_raw_log(
        self, *, dataset_id, dataset_version, raw_log_id, version_root_uri, params
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        # 1. Open the rosbag2/MCAP file (mcap.reader.make_reader)
        # 2. Decode per message encoding:
        #    - cdr: mcap_ros2.decoder.DecoderFactory decodes real ROS2 messages
        #      (no rclpy needed — uses the schema embedded in the MCAP file
        #      itself), recursively walked into plain dicts. nav_msgs/Odometry,
        #      sensor_msgs/Imu, sensor_msgs/BatteryState remap to flat fields;
        #      std_msgs/String re-parses .data as JSON (the §2 bridge format).
        #    - json: test-fixture bridge format, used as-is
        # 3. Topic discovery -> SensorModality mapping (camera/lidar/etc.)
        # 4. Timestamp alignment -> RawSensorFrameManifest list
        # 5. Robot-state topics (odom/imu/control/status) extracted separately
        #    as RobotState records (extract_robot_states()), not as frames
        # 6. Assemble RawLogManifest/RawLogFrameIndex, write to ArtifactStore
```

`BuildScenesJobHandler.run()` and everything downstream of it (`SceneBuilder`,
artifact registration, dataset-version update) needs **no code change** to
accept a real rosbag — it only ever depends on getting a
`RawLogManifest`/`RawLogFrameIndex` back, regardless of whether the adapter
behind that is nuScenes-mock or real MCAP. Same design payoff as
`ArtifactStore` making storage-backend swaps code-change-free
([ADR-002](../adr/002-object-storage-for-assets.md)).

Storage: locally recorded bags follow `RawSourceSettings`' independent-root
convention (`/data/raw/rosbag/...`); a published recording lives under
`{artifact root}/robot_runs/{run_id}/` (see
[Storage layout](../architecture/storage-layout.md) §3).

### 3.1 Materialization: ArtifactStore-backed recordings

`RosbagAdapter` stays storage-agnostic — it only ever opens a local
filesystem path (`mcap.reader.make_reader(open(path, "rb"))`), never
`s3://`, MinIO, `ArtifactStore`, or HTTP directly. A local recording path
reaches `RosbagAdapter` completely unchanged, exactly as described in §3.

A RobotRun's recording lives in ArtifactStore (published by the Recording
Publisher, §3.2), so Episode building materializes it to a local file
first:

```text
RobotRun.recording_artifact_id -> recording ArtifactRecord.uri (e.g. s3://...)
  -> materialize_recording() (sceneops_worker.robots.materialization)
       -- reads the object via ArtifactStore.read_bytes(), writes it to
          an execution-scoped local temp file (Python's own
          tempfile.TemporaryDirectory -- a fresh, uniquely-named
          directory per call), verifies it against the RobotRun's own
          registered ArtifactRecord checksum
  -> RosbagAdapter(local_path) -- identical to the local-path case
```

**Canonical recording invariant.** `BuildEpisodesJobHandler`
(`_extract_episode_source`) is the one caller wired to this today.
Whenever a `robot_run_id` is given, the RobotRun's recording ArtifactRecord
supplies the URI (unless an explicit `mcap_uri` overrides it) and its
checksum is verified against the bytes actually read
(`verify_local_recording_checksum` for a local path, `materialize_recording`'s
own check otherwise). A missing recording ArtifactRecord is inconsistent
canonical state and raises `RobotRunNotMaterializedError`. A bare
`mcap_uri` param with no `robot_run_id` makes no canonical-recording claim
and is not verified. `IngestRobotStatesJobHandler` reads the registered
recording URI without materializing it, so with only `robot_run_id` it can
read a local-backend recording but not an object-storage one; pass an
explicit local `mcap_uri` alongside `robot_run_id` in that case (§6).

**Lifecycle.** The materialized local copy is temporary and
execution-scoped: it exists only for the duration of the `async with
materialize_recording(...)` block (in practice, exactly as long as
`RosbagAdapter` needs to read it), and is deleted on that block's exit
whether the caller's code completed normally or raised — never left
behind by a normal exception. It is never a shared/cached path across
job executions: two independent executions materializing the same
`RobotRun` concurrently each get their own temp directory, with no
coordination between them. A crashed process (`SIGKILL`, container
death) can leave a materialized file behind with no `finally` having
run — this is treated as disposable temp data, not a canonical resource
requiring cleanup: nothing else in the platform reads it, and the
container/worker process lifecycle (an ephemeral filesystem with no
persistent volume backing the OS temp directory) already reclaims it on
the next container recreation.

**The canonical ArtifactStore object itself is immutable from this
boundary's perspective** — materialization only ever reads it
(`ArtifactStore.read_bytes`), never writes to or deletes it. Retrying
Episode building for the same `RobotRun` (the existing job/pipeline
`force`/idempotency semantics, unchanged) may materialize the recording
again; that's an expected, cheap re-read, not a correctness concern.

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
different bytes is a hard conflict and is never overwritten. Registering
the same manifest again is a no-op (`created=false`); a different manifest
for a registered `run_id` fails, and so does a manifest `robot_platform`
that contradicts the Robot's set platform. Registration never uploads,
moves or rewrites bytes.

`robot_run_root` defaults to the ArtifactSettings' `robot_run_root_uri`
(`{artifact root}/robot_runs`); the publisher reads its ArtifactStore
settings from `SCENEOPS_PUBLISHER_ARTIFACT__*`.

Write-once enforcement is "check, write, re-read" on an ArtifactStore
without conditional create. Two publishers racing on one `run_id` with
different bytes are detected (post-write verification, registration and
consumer checksums) rather than prevented; capture routing by
`robot_run_id` provides the single-publisher assumption.

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

# then dispatch `ingest_robot_states` (robot_run_id + the local mcap_uri) via
# POST /api/v1/jobs, or a raw_log_episode_building PipelineRun with the same
# robot_run_id, for episodes
```

Requires the nuScenes CAN bus expansion unzipped at
`data/raw/nuscenes/can_bus/` (a separate download from nuScenes mini).
Everything else is self-contained in the `ros2/` Docker image.

## 6. Current limitations

- **Binary sensor payloads aren't written to files.** `sensor_msgs/Image`/
  `PointCloud2` decode via CDR but `RawSensorFrameManifest.uri` stays empty
  — no camera/LiDAR-publishing ROS2 node exists yet to test a real write
  path against.
- **Publication and registration are explicit steps.** Nothing triggers
  the Recording Publisher from a finalized capture, or registration from a
  published manifest; published-but-unregistered manifests are not
  discovered automatically.
- **Recording consumers do not yet share one verified resolver.**
  `build_episodes` and `ingest_robot_states` still accept a bare `mcap_uri`
  param that is read unverified, and `ingest_robot_states` with only
  `robot_run_id` cannot read an object-storage recording (§3.1).
- **Recordings are read whole into memory** by the publisher, registration
  and materialization (`ArtifactStore.read_bytes`).
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

## 8. Design background

This capability was originally scoped as the largest remaining gap against
an internal roadmap, on the assumption it required designing a new
ingestion abstraction from scratch. In practice, `RAW_LOG_SCENE_BUILDING`'s
`build_scenes` job already used a `RawLogAdapter` Protocol
(`apps/worker/sceneops_worker/observations/adapters/base.py`) structurally
equivalent to what robot ingestion needed, and the raw-log schemas
(`RawLogManifest`/`RawLogFrameIndex`) already had unused placeholder values
aimed squarely at this case (`RawLogSourceFormat.ROSBAG`,
`RawLogSourceType.REAL_ROBOT_LOG`/`SIMULATOR_LOG`). The actual remaining
work was narrower: build `RosbagAdapter` as a second implementation of that
existing Protocol, plus the new `RobotState`/`Mission` entities runtime
telemetry needed that raw-log schemas don't model (§4). See
[ADR-005](../adr/005-ros2-vs-kafka-boundary.md) for the ROS2-vs-Kafka
layering decision this work depends on.
