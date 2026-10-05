# dataset-acquisition

Converts an external dataset into **L1 acquisition input** (ADR-007
§29.13), in two modes that share one event stream. It produces no canonical
data.

```text
dataset adapter (nuScenes)
  -> acquisition events   topic · message type (name, encoding, definition)
                          · CDR payload, serialized once · source time · per-topic sequence
  -> MCAP sink            finalized local MCAP, as the robot's own recorder would
                          have written it (batch mode)
  -> ROS 2 replay sink    the same payload bytes published on ROS 2 topics at the source's
                          pace (streaming mode); the platform's bridge, Kafka and capture
                          record them
```

SceneOps sees only the finished recording, through the normal RobotRun path.
With compose (`compose/acquisition.yaml`, profile `acquisition`) every step
is a one-shot container, and the MCAP moves between them through the
`acquisition-recordings` volume:

```bash
COMPOSE="docker compose --env-file .env.local --profile acquisition"
$COMPOSE run --rm dataset-acquisition nuscenes --dataroot /input/nuscenes \
    --version v1.0-mini --source-unit scene-0061 --output /recordings/scene-0061.mcap
$COMPOSE run --rm recording-publisher check --mcap-path /recordings/scene-0061.mcap
$COMPOSE run --rm recording-publisher publish --mcap-path /recordings/scene-0061.mcap \
    --run-id RUN --robot-id ROBOT --source-kind file      # prints manifest_uri (JSON)
curl -X POST localhost:8000/api/v1/robot-runs:register \
    -H 'Content-Type: application/json' -d '{"manifest_uri": "..."}'
```

## Replay (streaming mode)

```bash
COMPOSE="docker compose --env-file .env.local --profile acquisition --profile ros2 --profile streaming"
# bridge and capture run in the platform's ros2 container (see
# docs/architecture/streaming-transport.md); then:
$COMPOSE run --rm dataset-replay nuscenes --dataroot /input/nuscenes \
    --source-unit scene-0061 --replay --rate 2
```

`--replay` replaces `--output`. The replay image (`sceneops-platform/dataset-replay:local`,
target `replay` of this Dockerfile) is ROS 2 Jazzy plus this tool, built from the
same `uv.lock`; it contains no SceneOps package and no credentials, and meets the
platform only over DDS on the compose network. `make acquisition-image-check`
verifies that for both images.

The sink publishes each event's CDR payload as raw bytes (`publisher.publish(bytes)`):
nothing is deserialized or reserialized and no timestamp is touched, so the bytes on
the wire are the bytes the MCAP sink writes. Pacing uses `source_time_ns` only as a
schedule: an event is published `(source_time - first_source_time) / rate` after the
start (`--rate 0` publishes without pacing). Before the first message every topic must
have a matched subscriber (`--wait-subscribers-seconds`, default 60) or the replay fails;
delivery is reliable with unbounded sender history, `/tf_static` is latched, and after
the last message the sink waits for every sample to be acknowledged and fails if any is
not. It prints one `replay_summary` JSON line (counts per topic, elapsed time, the
largest lag behind schedule, the largest payload).

A ROS 2 topic carries no per-topic publisher counter, so the replay sink drops the
event's `sequence`; the bridge assigns a transport sequence on arrival.

## Boundary

- Its own uv project and `uv.lock`, outside the root workspace.
- Depends on `nuscenes-devkit`, `rosbags` (ROS 2 Jazzy type store and CDR
  serializer), `mcap` and `numpy` only. It depends on **no** SceneOps
  package. `tests/test_import_boundary.py` checks sources, declared
  dependencies, the lockfile and the environment (I-36).
- Never publishes, registers, talks to PostgreSQL, Object Storage or the
  SceneOps API, and never writes manifests or ArtifactRecords.
- Knows nothing about Scene, Episode, DatasetVersion, sampling, keyframes or
  channel semantics. A source unit selects input. It is not a Scene boundary.

## Usage

```bash
dataset-acquisition nuscenes --dataroot DATAROOT --version v1.0-mini \
    --source-unit scene-0061 --output OUT.mcap [--channels camera,lidar,pose,can,mission]
```

**Container.** `make acquisition-image` builds
`sceneops-platform/dataset-acquisition:local` (target `batch`) and
`sceneops-platform/dataset-replay:local` (target `replay`) from this directory
alone, as the build context. Dependencies install from this `uv.lock` with `--frozen`.
The runtime stage holds only the virtualenv: no compiler, uv, sources or
tests. It runs as a non-root user with the CLI as its entrypoint. The
compose service mounts the dataset read-only at `/input/nuscenes`
(`ACQUISITION_NUSCENES_ROOT`, default `./data/raw/nuscenes`). It writes to
the `acquisition-recordings` named volume at `/recordings` and runs with
`network_mode: none`, so it holds no SceneOps credentials and could not
reach SceneOps if it tried. `make acquisition-image-check` verifies inside
the image that no SceneOps package is installed, importable or loaded.

**Local development.** `make acquisition-sync` creates the project venv;
`uv run --project tools/dataset-acquisition dataset-acquisition ...` runs
the same CLI.

The command prints a JSON summary (path, sha256, size, message and per-topic
counts, first/last `log_time`). The output is write-once. It is written to
`<output>.partial`, fsynced, then atomically renamed. An existing output is
never overwritten, and a leftover `.partial` is reported, not reused or deleted.

## nuScenes mapping

A source unit is a nuScenes scene name. It selects every camera and lidar
`sample_data` the dataset associates with that scene's samples, both key
frames and sweeps. It also selects their ego poses, the scene's CAN bus
extract (`can_bus/<scene>_{pose,ms_imu,vehicle_monitor}.json`) and two
mission events. Radar is not converted.

| Topic | Type | Source | Frame |
|---|---|---|---|
| `/camera/<pos>/image/compressed` | `sensor_msgs/msg/CompressedImage` | camera `sample_data`; JPEG file bytes passed through, `format=jpeg` | `cam_<pos>` |
| `/camera/<pos>/camera_info` | `sensor_msgs/msg/CameraInfo` | `calibrated_sensor.camera_intrinsic`; one per image, same header | `cam_<pos>` |
| `/lidar/top/points` | `sensor_msgs/msg/PointCloud2` | `LIDAR_TOP` `.pcd.bin` file bytes passed through | `lidar_top` |
| `/tf_static` | `tf2_msgs/msg/TFMessage` | `calibrated_sensor` of each converted sensor | `base_link` → sensor |
| `/tf` | `tf2_msgs/msg/TFMessage` | `ego_pose`, one per `sample_data` of the unit (all sensors) | `map` → `base_link` |
| `/vehicle/odom` | `nav_msgs/msg/Odometry` | CAN `pose` | `odom` |
| `/vehicle/imu` | `sensor_msgs/msg/Imu` | CAN `ms_imu` | `imu` |
| `/vehicle/status` | `sensor_msgs/msg/BatteryState` | CAN `vehicle_monitor.battery_level` | — |
| `/vehicle/control` | `std_msgs/msg/String` (JSON) | CAN `vehicle_monitor` steering / throttle / brake | — |
| `/mission/status` | `std_msgs/msg/String` (JSON) | synthetic `running` / `completed` | — |

`<pos>` is the channel name without `CAM_`, lower-cased (`CAM_FRONT_LEFT` →
`front_left`). Channel groups (`--channels`): `camera`, `lidar`, `pose`
(`/tf`), `can`, `mission`. `/tf_static` is written whenever a camera or
lidar is converted.

Representation choices:

- **Camera.** `CompressedImage` carries the source JPEG unchanged, with no
  decode or re-encode. `CameraInfo` uses `plumb_bob` with zero distortion,
  identity `R` and `P = [K | 0]`, because nuScenes publishes undistorted
  images with a 3×3 intrinsic. Camera frames use nuScenes' optical
  convention (z forward, x right, y down).
- **Lidar.** `PointCloud2.data` is the `.pcd.bin` file verbatim: little
  endian `float32` `x, y, z, intensity, ring` (`point_step` 20, `height` 1).
  `is_dense=false`, because the source does not claim every point is valid.
  The canonical Scene payload layout is not decided here (ADR-007 Q2).
- **Calibration.** Sensor extrinsics are `base_link → <sensor>` static
  transforms. A unit whose sensor has more than one calibration fails
  loudly, because one recording carries one static calibration per sensor.
- **Ego pose.** nuScenes localization (`ego_pose`) is `map → base_link` on
  `/tf`. CAN `pose` stays separate, as vehicle odometry on `/vehicle/odom`.
- **Messages without a standard type.** `/vehicle/control` and
  `/mission/status` use JSON in `std_msgs/String`, the same shape as the
  ROS 2 CAN replay node. The mission payload is `mission_id`
  (`mission-<unit>`), `operation_state` and `source_timestamp_ns`. It
  carries no robot identity, which is a publication input.
- **Message construction.** Unset fields take rclpy's defaults (zeros,
  empty, and declared defaults such as `Quaternion.w = 1`).

## Timing

Each timing fact keeps its own meaning (ADR-007 §29.5 R4, I-33):

| Fact | Value |
|---|---|
| Source observation time | Inside the payload, unrewritten: `Header.stamp` (or the transforms' stamps, or the JSON `source_timestamp_ns`) = the source's integer µs Unix-epoch timestamp × 1000 |
| MCAP `log_time` | Simulated receive time = the event's source time, with zero simulated latency. Never the conversion-time clock |
| MCAP `publish_time` | `= log_time` |
| MCAP `sequence` | Per-topic publisher counter 1, 2, … in write order |

Values with no source time sit on the source timeline. `/tf_static` is
stamped and written at the unit's first source time, before every
observation. The `running` and `completed` mission events sit at the unit's
first and last source times (R11).

**Order and determinism.** Messages are ordered by source time. At equal
times `/tf_static` and `running` come first and `completed` comes last, then
messages sort by topic, then by source order within a topic. No wall clock,
random id or unordered iteration reaches the stream. For one dataset
revision, source unit, channel selection and tool version, the output is
byte-identical. nuScenes mini `scene-0061` gives the same sha256 on every
run.

## Acquisition origin

The sink writes one MCAP metadata record, `sceneops.acquisition_origin`,
with `tool`, `tool_version`, `source_format`, `source_version`,
`source_unit` and `channel_groups`. It is for inspection and lineage
only. Canonicalization, RobotRun identity and registration never read it
(ADR-007 §29.8, I-32).

## Tests

```bash
make acquisition-test        # synthetic dataroot + replay scheduling + import boundary + real nuScenes mini
make acquisition-image-check # boundary check inside both built images
make e2e-batch-canonical     # containers + FastAPI: acquire -> check -> publish -> register -> Scenes + Episodes
make e2e-streaming-equivalence # replay -> ROS 2 -> bridge -> Kafka -> capture, equivalent to batch
```

`tests/test_nuscenes_mini.py` converts a full real scene and compares every
message with the source tables, files and CAN extract. It is skipped when
`data/raw/nuscenes` (or `NUSCENES_DATAROOT`) is absent.

## Reference corpus

`dataset-acquisition reference {inspect,verify,prepare}` works on a versioned
corpus of fixtures (`config/reference/<corpus>/`, see
[docs/development/reference-corpus.md](../../docs/development/reference-corpus.md)):
it fingerprints the source, materializes batch MCAPs into a local cache
(write-once) and verifies them against `corpus.lock.json`. Only
`prepare --update-lock` writes the lock; every disagreement fails. L1
conformance is checked by the platform's publisher `check`, because this tool
depends on no SceneOps package. Run it through `make reference-data-bootstrap` /
`make reference-data-verify`.

## Limitations

- The replay sink needs a ROS 2 runtime (the replay image); `--replay` in the plain
  image fails with a clear error.
- nuScenes only. Radar, annotations and keyframe groupings are not
  converted, because labels are not acquisition data (ADR-007 §29.15).
- A whole unit's plan (record metadata, not payloads) is held in memory.
  Payloads are read and serialized one message at a time.
