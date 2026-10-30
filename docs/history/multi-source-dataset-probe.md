# Multi-source dataset probe: nuScenes and LeRobot (M1)

> Point-in-time comparison of the two external sources the multi-source target names. It
> records what was read from real data at the given date; it is evidence for
> [ADR-015](../adr/015-multi-source-platform.md), not a contract. Coupling in the current
> platform is in the [audit](./multi-source-architecture-audit.md).

```text
date        2026-10-16
branch      refactor/multi-source-platform
HEAD        bfd077e
nuScenes    v1.0-mini, local: data/raw/nuscenes (9.4 GB), read with python3 -I (json only)
LeRobot     Hugging Face dataset repos, codebase_version v3.0, fetched read-only into the
            session scratchpad (not committed):
              lerobot/pusht  revision 7628202a2180972f291ba1bc6723834921e72c19
                meta/info.json, meta/stats.json, meta/tasks.parquet,
                meta/episodes/chunk-000/file-000.parquet, data/chunk-000/file-000.parquet
              lerobot/aloha_static_coffee  meta/info.json only
              lerobot/svla_so101_pickplace meta/info.json only
            read with the workspace pyarrow (python -I); the lerobot SDK was not used
```

Labels: **VERIFIED** = read from the files above; **UNVERIFIED** = assumption or documented
behaviour not checked on data in this probe.

## 1. Side-by-side

| Dimension | nuScenes v1.0-mini | LeRobot v3.0 |
| --- | --- | --- |
| Natural unit | `scene` (~20 s drive), 10 in mini | `episode_index` (one demonstration), 206 in pusht |
| SceneOps domain fit | **Scene**: multi-sensor, calibrated, posed, spatio-temporal | **Episode**: observation / action sequence with task |
| Unit identity in source | scene `token` (32 hex) + `name` (`scene-0061`) | integer `episode_index`, dense from 0 |
| Dataset revision | version string `v1.0-mini`; no content hash shipped | HF repo commit sha; `codebase_version` = format version |
| Physical layout | JSON tables (`v1.0-mini/*.json`), one file per sensor reading (`samples/`, `sweeps/`), CAN JSON per scene | episodes packed into chunked Parquet files; one concatenated MP4 per camera per file; JSON / Parquet metadata |
| Time representation | integer **µs since Unix epoch**, per reading | **float32 seconds, episode-relative**, starting at 0.0, on a fixed fps grid |
| Synchronization | asynchronous: each sensor at its own rate; keyframes group one reading per sensor at ~2 Hz | already synchronous: every feature has one value per frame |
| Calibration / frames | per sensor `calibrated_sensor` (extrinsic, camera intrinsic) | none (no extrinsics, no frames) |
| Poses | `ego_pose` per sensor reading | none (joint positions only) |
| Payloads | JPEG files (camera), `.pcd.bin` (lidar), `.pcd` (radar) | numeric vectors inline in Parquet; images as frames inside MP4 (AV1 in the probed sets) |
| Labels | 3-D boxes on keyframes (`sample_annotation`), tracked `instance` | per-frame `next.reward` / `next.done` / `next.success` (dataset-dependent); task strings |
| Task semantics | none | `tasks.parquet` (task_index → string) |

## 2. nuScenes v1.0-mini (VERIFIED)

Table sizes:

```text
scene 10   sample 404   sample_data 31,206 (4,848 keyframe, 26,358 sweep)   ego_pose 31,206
calibrated_sensor 120 (60 with camera_intrinsic)   sensor 12   log 8   map 4
sample_annotation 18,538   instance 911   category 23   attribute 8   visibility 4
sample_data fileformat: jpg 14,008, pcd 17,198
CAN bus: 7,832 files under can_bus/ (per-scene extracts)
```

Sensors: 6 cameras, `LIDAR_TOP`, 5 radars. `ego_pose` is one row per `sample_data`
(31,206 = 31,206): every reading carries its own vehicle pose, and every `ego_pose.timestamp`
equals its `sample_data.timestamp` exactly (31,206 of 31,206, maximum difference 0 µs).
Sensor readings and ego poses therefore share one source clock.

`scene-0061` (the platform's smoke fixture), rates over the scene's `sample_data`:

```text
channel           readings   rate
CAM_* (6)         216–224    11.2–11.6 Hz
LIDAR_TOP         382        19.9 Hz
RADAR_* (5)       243–262    12.6–13.6 Hz
total             2,963 sample_data, 468 of them keyframe (= 39 samples × 12 sensors)
samples           39 over 19.15 s (≈ 2 Hz)
annotations       4,699 on those 39 samples
boundary          sample_data start 43.1 ms before the first sample and end 29.3 ms
                  after the last one
```

CAN extracts for `scene-0061` (each row has integer `utime` µs):

```text
pose 938 rows 49.0 Hz · ms_imu 1,899 rows 99.1 Hz · steeranglefeedback 1,797 rows 93.8 Hz
zoesensors 18,284 rows 954.8 Hz · zoe_veh_info 1,904 rows 99.4 Hz · vehicle_monitor 38 rows 1.9 Hz
meta (dict of per-message summaries) · route (964 entries, not a time series)
```

Observations:

- **Time.** All source times are integer µs on one epoch clock; promotion to ns is exact
  (`sceneops_core.provenance.source_time.promote_to_ns`). The camera / lidar / radar
  readings and CAN rows share that clock (VERIFIED as the same unit and epoch range;
  UNVERIFIED: that CAN and sensor clocks are hardware-synchronized).
- **Boundary.** A scene's readings extend beyond its first and last keyframe by tens of
  milliseconds, so a Scene window derived from keyframes would cut source observations.
  The boundary must be "every `sample_data` the source assigns to the scene's samples",
  which is the ADR-007 §27.4 v1 interpretation.
- **Keyframes are source structure.** `is_key_frame` and the `sample` grouping (one reading
  per sensor) map directly to `SceneManifest.groups` (`kind = keyframe`); the schema already
  has that construct.
- **Reference recording comparison.** The locked MCAP for `scene-0061`
  (`prepare-log.jsonl`) has 1,318 images, 382 point clouds, 2,963 `/tf` ego transforms,
  938 `/vehicle/odom`, 1,899 `/vehicle/imu`, 38 `/vehicle/status`, 38 `/vehicle/control`,
  2 `/mission/status` and no radar topic. The `/tf` count equals the scene's total
  `sample_data` count across all 12 sensors (1,700 camera + lidar + 1,263 radar), which is
  consistent with ego poses being kept for radar readings whose observations are dropped.
  Native ingestion would keep 1,263 radar observations, 39 keyframe groups, 5 further CAN
  streams and every token the conversion discards.
- **Native identity.** Every row has a stable token; `sample_data.token` is a natural native
  observation reference and `sample.token` a natural keyframe-group reference.
- **Revision.** `v1.0-mini` names a release, not content. A content checksum over the
  published files is the only reliable revision pin (UNVERIFIED: whether nuScenes ever
  re-issued files under the same version string).

Cross-check of the 10 locked reference MCAPs against the source, same date and HEAD
(VERIFIED, by a throwaway reader that imports no SceneOps package; the tool was not kept):

- **Byte equality for converted channels.** Camera payloads are the nuScenes JPEG files
  byte for byte, and lidar PointCloud2 data is the `.pcd.bin` file byte for byte. CAN
  values, intrinsics and extrinsics equal the source tables. Battery percentage is the
  float32 of `battery_level / 100`.
- **Readings with equal timestamps.** Five `sample_data` timestamps in mini are shared by two
  readings (`scene-0103`, `scene-0757`, `scene-1094`, and two in `scene-1100`). The source
  defines no order between them. In `scene-1094` the pair (`LIDAR_TOP` and
  `RADAR_BACK_RIGHT` at 1542800853097255 µs) has two distinct ego poses about 0.5 mm apart,
  so a time-based pose association at that instant is ambiguous. A native Scene should link
  each reading to its own `ego_pose_token`.
- **Recording-derived Scene windows include radar poses.** The MCAP keeps a `/tf` ego pose
  for every radar reading it drops, so a window bounded by poses extends beyond the camera
  and lidar observations (`scene-0061`: 7.6 ms before the first camera frame).
- **Labels sit on lidar keyframes.** With a view anchored on camera frames and nearest-lidar
  association, between 0 % (`scene-0796`, `scene-0916`) and 86 % (`scene-0757`) of a
  fixture's annotations attach (`scene-0061`: 1,045 of 4,699). A view meant for detection
  evaluation should anchor on keyframes.

## 3. LeRobot v3.0 (VERIFIED for pusht; info.json only for the other two)

`lerobot/pusht` metadata:

```text
codebase_version v3.0   robot_type unknown   fps 10   total_episodes 206   total_frames 25,650
total_tasks 1 ("Push the T-shaped block onto the T-shaped target.")
data_path   data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet
video_path  videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4
repo files  8 (one data file, one video file, metadata)
```

`data/chunk-000/file-000.parquet`: 25,650 rows = **all 206 episodes in one file**.

```text
observation.state  fixed_size_list<float32>[2]
action             fixed_size_list<float32>[2]
episode_index      int64        frame_index  int64       index  int64 (global row)
timestamp          float32      task_index   int64
next.reward        float32      next.done    bool        next.success  bool
```

Episode 0: 161 frames, `frame_index` 0…160, `timestamp` 0.0, 0.10000000149011612, …, 16.0;
frame spacing 0.1 s with float32 jitter (0.099999 / 0.1). `next.done` is true on the
**last two** frames of every one of the 206 episodes (159 and 160 for episode 0), and
`next.success` is false on every frame of the file, so neither can be read as an
episode outcome without dataset-specific interpretation.

`meta/episodes/chunk-000/file-000.parquet` (206 rows), per episode: `data/chunk_index`,
`data/file_index`, `dataset_from_index`, `dataset_to_index` (global row range),
`videos/<key>/{chunk_index,file_index,from_timestamp,to_timestamp}` (seconds within the
concatenated MP4: episode 0 is 0.0–16.1, episode 1 is 16.1–27.9), `tasks` (list of
strings), `length`, and per-feature statistics.

Other datasets (info.json only):

```text
aloha_static_coffee    robot_type aloha            fps 50  50 episodes  55,000 frames
                       4 cameras (480×640, av1), observation.state / observation.effort /
                       action float32[14], names = {"motors": [...]}
svla_so101_pickplace   robot_type so100_follower   fps 30  50 episodes  11,939 frames
                       2 cameras (480×640), observation.state / action float32[6],
                       names = ["shoulder_pan.pos", …]   (a list, not a dict)
```

Observations:

- **Time.** LeRobot stores no absolute time and no per-stream source timestamp. The only
  clock is the episode-relative frame grid. `timestamp` is float32, so it is not exact
  (0.1 s is stored as 0.10000000149…). `frame_index / fps` is exact as a rational, but not
  an integer number of ns for every fps (30 fps → 33,333,333.3 ns).
- **Already aligned.** In SceneOps terms a LeRobot episode is a derived, aligned
  representation: there is nothing asynchronous left to preserve. A canonical Episode built
  from it keeps the frame grid as its source timing; aligning it again at the native fps
  must be an identity operation.
- **Unit boundary is a row range and a video time range**, not a file. Native references
  to an episode need `(data file, row range)` and, per camera, `(video file, from/to
  timestamp)`.
- **Feature naming is not uniform.** `names` is a dict (`{"motors": [...]}`) in pusht and
  aloha and a plain list in svla_so101. A reader must accept both and record the source
  form.
- **Revision.** The HF commit sha pins content; `codebase_version` pins the format.
- **Signals beyond observation / action** (`observation.effort`, `next.reward`,
  `next.done`, `next.success`) vary by dataset; `next.*` are per-frame signals whose
  meaning is dataset-specific (see the pusht `next.done` / `next.success` values above), not
  task-level outcomes. A canonical Episode keeps them as recorded streams and derives no
  `EpisodeOutcome` from them.

UNVERIFIED for LeRobot: video decoding and frame-exact seeking in the concatenated MP4s (no
MP4 was downloaded); datasets with several data files or chunks; episodes with several
tasks; v2.x datasets (per-episode files, different metadata); data files of
aloha_static_coffee and svla_so101_pickplace.

## 4. Source boundaries and mapping consequences

```text
                     nuScenes                         LeRobot
SceneOps domain      Scene (not Episode)              Episode (not Scene)
unit key             scene name or token              episode_index
source clock         nuscenes µs epoch → ns, exact    episode frame grid; needs a declared
                                                       normalization rule (ADR-015 §6.2)
clock origin         absolute (Unix epoch) for        relative to each episode's own start;
                     sensors + ego poses; CAN not     no absolute anchor exists, and two
                     verified to share it             episodes' times are not comparable
asynchronous data    yes: keep every reading          no: one value per frame
structure to keep    keyframe groups, tokens,         tasks, per-frame next.* signals,
                     calibration, ego poses           feature names
labels               sample_annotation → LabelSet     none (next.* are signals, not labels)
payload location     one native file per reading      row in a shared Parquet; frame in a
                                                       shared MP4
```

- Neither source fits both domains. Forcing nuScenes into Episodes (no task) or LeRobot
  into Scenes (no calibration, no poses) would invent semantics.
- nuScenes payloads are addressable per native file, so a canonical payload reference can
  name the native file directly. LeRobot payloads are not: numeric values are best carried
  inline in the canonical manifest (as recording-derived Episodes already do), and images
  need a frame reference into a native video.
- Both sources need content-pinned revisions that the source itself does not fully provide
  (nuScenes) or provides outside the files (LeRobot's HF sha).

## 5. Open points this probe could not settle

- Frame-accurate decode of AV1 MP4 frames in the platform image (no video was fetched).
- Whether nuScenes CAN and sensor timestamps share one hardware clock.
- Full-size nuScenes object counts (file count, annotation count) for storage sizing.
- LeRobot v2.x layout support, and datasets whose episodes span several data files.
