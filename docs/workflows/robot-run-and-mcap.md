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
(`apps/worker/sceneops_worker/observations/adapters/base.py`), the same
abstraction `NuScenesRawLogMocker` implements for structured-dataset
ingestion — registered under `RawLogSourceType.REAL_ROBOT_LOG` in
`build_scenes.py`'s adapter factory.

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

Storage: rosbag/MCAP originals don't have a dedicated prefix in
[Storage layout](../architecture/storage-layout.md) yet — the working
convention, following `RawSourceSettings`' independent-root pattern for raw
datasets, is `/data/raw/rosbag/{robot_id}/{run_id}.mcap` locally.

## 4. Entity relationships

```text
Robot        robot_id, name, platform -- static metadata
RobotRun     robot_id + raw_log_id, 1:1 -- "this robot's this run produced this raw log"
Mission      mission_id, robot_id, status -- referenced by RobotState.mission_id
```

`RobotRun` is not the same concept as `PipelineRun` (see
[Data model](../architecture/data-model.md) §5) — `PipelineRun` is a
SceneOps-internal processing execution; `RobotRun` is a physical robot
execution (one rosbag corresponds to one `RobotRun`).

`SceneRecord.parent_scene_id`/`lineage` (JSONB) is reused as-is to track
which `RobotRun` a scene came from — no new lineage mechanism was built for
robot data. The Scene-domain quality gate mechanism
(`PipelineTaskQualityRule`) is equally reusable for robotics-specific
validation (e.g. "does `CAM_FRONT`/`LIDAR_TOP` exist," "sensor timestamp
tolerance") by adding a new rule to `validate_scene`, without new
infrastructure.

## 5. Quickstart

```bash
make local-up
make ros2-up                        # ROS2 Jazzy sandbox (rclpy, rosbag2, MCAP storage plugin)
make e2e-robot-can-replay           # CAN replay -> record -> register -> ingest, verified via API
```

Or step by step:

```bash
make ros2-can-replay-record SCENE=scene-0061 RATE=5.0
make worker-register-robot-run ROBOT_ID=robot-1 RUN_ID=run-1 MCAP_URI=/data/raw/rosbag/scene-0061/scene-0061_0.mcap
# then dispatch `ingest_robot_states` via POST /api/v1/jobs, same as any other job
# or dispatch a raw_log_episode_building PipelineRun with the same raw_log_id, for episodes
```

Requires the nuScenes CAN bus expansion unzipped at
`data/raw/nuscenes/can_bus/` (a separate download from nuScenes mini).
Everything else is self-contained in the `ros2/` Docker image.

## 6. Current limitations

- **Binary sensor payloads aren't written to files.** `sensor_msgs/Image`/
  `PointCloud2` decode via CDR but `RawSensorFrameManifest.uri` stays empty
  — no camera/LiDAR-publishing ROS2 node exists yet to test a real write
  path against.
- **No pre-registration flow for `Robot`/`RobotRun`.** `IngestRobotStatesJobHandler`
  assumes both already exist in the DB. There's no dedicated API/CLI wizard
  for creating them ahead of a CAN replay — today that's a direct `POST
  /robots` + `POST /robot-runs` call, or `make worker-register-robot-run`.
- **`/vehicle/control` uses a JSON bridge, not a real `.msg` package** — a
  deliberate scope cut to avoid a `colcon` build step; revisit if real
  robot integration needs a first-class message type.
- **No live robot control.** This is batch ingestion of a recording
  (replay -> record -> decode -> ingest), not real-time command/control —
  see [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) for the intended
  boundary once/if a streaming path is built.
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
