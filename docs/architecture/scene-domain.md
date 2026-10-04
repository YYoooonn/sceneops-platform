# Scene Domain

A **Scene** is SceneOps' canonical spatiotemporal environmental observation
unit: within a selected boundary (a window of a registered RobotRun
recording, over a configured set of recording channels) it keeps every
source observation with its own source timing, verbatim source channel
identity, payload, calibration, coordinate-frame semantics and source
poses, together with source and producer provenance. Scenes are produced
only from registered recordings ([ADR-007](../adr/007-canonical-ingestion-architecture.md) §29, §30).

Three things make up a Scene ([ADR-007](../adr/007-canonical-ingestion-architecture.md) §3):

```text
SceneManifest   immutable, canonical JSON description of the unit      (Object Storage)
SceneRecord     DatasetVersion membership + searchable projection      (PostgreSQL)
payloads        observation bytes, one OBSERVATION_PAYLOAD ArtifactRecord each (Object Storage)
```

The manifest is authoritative; the record is an index over exactly one
registered manifest revision.

## 1. SceneManifest v1

Schema: `sceneops_core.scenes.schemas.manifests` (`schema_version =
"sceneops.scene_manifest/v1"`). Strict: unknown fields are rejected at every
level, and there is no free-form metadata. A source fact the schema cannot
express is a contract amendment.

```text
SceneManifest
  schema_version
  lineage              source: RecordingSegmentSource (robot_run_id,
                         recording_artifact_id, recording_checksum, source_clock,
                         [start_timestamp_ns, end_timestamp_ns), unit_key)
                       producer: ProducerInfo (fingerprint re-derived on parse)
  coordinate_frames[]  frame_id (verbatim) + role: world | ego | sensor
  channels[]           channel (verbatim source identity), modality
                       (camera | lidar | radar | other), frame_id,
                       source_clock, sensor_id?
  calibrations[]       per channel: extrinsic (channel frame in a parent frame),
                       camera_intrinsic?
  observations[]       observation_id, channel, timestamp_ns, payload,
                       calibration_id?, ego_pose_id?, image_size?
  poses[]              timestamped source transforms (e.g. ego poses),
                       each with its source_clock
  groups[]             source-defined groupings: kind = keyframe, timestamp_ns,
                       source_clock, observation_ids (at most one per channel)
  annotations[]        source boxes: timestamp_ns, source_clock,
                       category (verbatim), instance_id?,
                       box (frame, center, size, rotation, velocity?),
                       attributes, group_id?
```

Rules the schema enforces:

- **Observations are primary.** Every observation inside the boundary is
  kept, whether or not a keyframe references it. A keyframe group is a
  non-lossy index over observations that keep their own timestamps; it never
  re-times or replaces them. Sampling, nearest-frame association, pose
  interpolation and synchronization are derived workflows.
- **Source time** is an integer nanosecond count
  (`sceneops_core.provenance.source_time`) in exactly one declared clock,
  never synchronized or converted. An observation's clock is its channel's
  `source_clock` (observation → channel → clock), so channels of one Scene
  may use different clocks. A timestamped structure no channel owns — a
  pose, a keyframe group's reference time, an annotation — declares its own
  `source_clock`. A keyframe may group observations whose channels use
  different clocks; each member keeps its own.
- **Boundaries.** A Scene's boundary is its segment's half-open window
  `[start, end)` in the segment clock, the segmentation clock the producer's
  build configuration declares (`mcap_log_time`, `mcap_publish_time` or a
  declared source clock). Every timestamp counted in that clock must lie
  inside it, and timestamps in other clocks are not compared with it. The
  window is never derived from the RobotRun's `started_at` / `ended_at`.
- **Source identity is verbatim.** Channels, frames and categories are the
  source's own names; `modality`, frame `role` and `sensor_id` are canonical
  semantics added alongside them, never instead.
- **Conventions are fixed** by the schema version and carried in field
  names: translations in metres (`*_m`), unit quaternions `[w, x, y, z]`
  (`rotation_wxyz`), box size `[width, length, height]` (`size_wlh_m`), and a
  `FrameTransform` maps points in `child_frame_id` into `parent_frame_id`.
- **Payloads** are strict `PayloadRef`s (`sceneops_core.artifacts.schemas.payload`):
  `artifact_id`, `sha256` `checksum`, `size_bytes` and a `media_type` that
  fully specifies encoding and layout. A reference names the payload's
  `OBSERVATION_PAYLOAD` ArtifactRecord, never a location:
  `artifact_id → ArtifactRecord → uri → ArtifactStore`. Moving payload bytes
  updates ArtifactRecords and never changes a manifest or its checksum. A
  reader requires the ArtifactRecord's checksum, size and media type to
  equal the reference's (`sceneops_worker.scenes.payloads`) and dispatches
  on `media_type`.
- **References resolve** (channel frames, calibration channel/frame,
  ego-pose role, group members, annotation group and box frame), and every
  list has one canonical order, with unique ids. Timestamped lists other than
  observations are ordered by `(source_clock, timestamp_ns, id)`, so the
  order never interleaves clocks.
- **No membership.** A manifest has no `scene_id`, DatasetVersion, status or
  execution context, so identical source + producer + build configuration
  produce byte-identical manifests in any DatasetVersion.
- **No location.** Provenance names the RobotRun and pins its recording
  bytes by checksum; it carries no recording URI. Acquisition-origin
  metadata inside the recording never reaches a manifest.

Bytes go through `SceneManifest.to_canonical_bytes()` (the shared canonical
JSON serializer) and `load_canonical_scene_manifest()`, which rejects bytes
that parse but are not already canonical.

## 2. SceneRecord

Schema: `sceneops_core.scenes.schemas.records`; table `scenes`.

```text
scene_id               deterministic: f(dataset_id, dataset_version, "scene", robot_run_id, unit_key)
dataset_id, dataset_version     FK → dataset_versions (RESTRICT)
robot_run_id           the source RobotRun; FK → robot_runs (RESTRICT)
unit_key               the producer's unit key within the recording (segment-<k>)
producer_fingerprint
manifest_artifact_id   FK → artifacts (RESTRICT): the current revision
manifest_checksum
window_clock, window_start_timestamp_ns, window_end_timestamp_ns
                       the segment window [start, end) in window_clock (NOT NULL,
                       non-empty)
observed_channels, observation_count, keyframe_count, annotation_count
registered_at, updated_at
```

There is no status column. A record exists if and only if the unit is
registered; its current revision is exactly `manifest_artifact_id`, never
"the latest manifest". `scene_id` comes from
`sceneops_core.provenance.canonical_unit_id`: a hash of a canonical identity
document, so it never depends on storage location, producer configuration
or revision. The same source unit in two DatasetVersions is two Scenes.

The window columns are a projection of the declared segment window, never
of observation extent, and never define identity. SceneOps does not
compute a cross-clock interval for any Scene.

## 3. Registration: the only writer

`REGISTER_SCENES` (`sceneops_worker.scenes.registration`) takes a
DatasetVersion and the ids of `SCENE_MANIFEST` ArtifactRecords. It is the
only writer of `scenes` rows and of the DatasetVersion Scene summary.

```text
verify   each artifact is a SCENE_MANIFEST; bytes match its size + checksum;
         canonical strict parse; every PayloadRef resolves to an
         OBSERVATION_PAYLOAD ArtifactRecord with exactly its checksum,
         size_bytes and media_type
scope    a registration covers one RobotRun, carries one producer fingerprint, and
         must have been built from that RobotRun's registered recording bytes
apply    one transaction under the DatasetVersion row lock, on the recording
         scope (DatasetVersion, robot_run_id): empty → insert · same fingerprint →
         no-op (whole scope) · different → conflict, or replace=True: delete ids
         absent from the new set, repoint reused ids, insert new
         recompute the DatasetVersion summary from membership; commit
```

Any failure rolls the whole registration back; units are never skipped.
Builds and manifest reads happen before the lock; every decision is made
after it, so concurrent registrations into one DatasetVersion serialize.
A recording build that yields no units fails.

Producers own their bytes: a producer writes a manifest with
`SceneArtifactStore.publish_canonical_manifest` (write-once, checksum-qualified
key, see [Storage layout](./storage-layout.md)) and registers its
`SCENE_MANIFEST` ArtifactRecord with its own execution lineage, after
registering the `OBSERVATION_PAYLOAD` ArtifactRecords its manifest
references. Registration never writes manifest or payload bytes or
ArtifactRecords.

## 4. Quality and readiness

`VALIDATE_SCENE` / `PROFILE_SCENE` take `scene_ids`, read each Scene at the
revision its record points to (`sceneops_worker.scenes.resolver`), and write
a per-scene run record that pins that revision (`manifest_artifact_id` +
`manifest_checksum`). They never write a SceneRecord or DatasetVersion state.
Validation reports per-channel aggregates: required channels without
observations (blocking), declared channels without observations, keyframes
missing a required channel (optionally blocking), and missing calibration,
intrinsics, image size or ego poses.

Readiness (`sceneops_core.scenes.readiness`) is derived from the newest
succeeded validation run **of the revision being assessed**; runs of any
other revision are ignored, so a replaced Scene is `unknown` until its new
revision is validated. See [Quality and run records](./quality-and-runs.md)
§2.

## 5. Derived consumers

Everything downstream of registration reads canonical Scenes through a pin:

- `BUILD_SCENE_INDEX` / `BUILD_DATASET_MANIFEST` derive index entries from
  SceneRecords; each entry pins `manifest_artifact_id`, `manifest_checksum`
  and the manifest URI. They do not write DatasetVersion summary counts.
- Detection projects source keyframes into samples
  (`sceneops_worker.scenes.keyframes`), reading each Scene at the revision
  the dataset manifest pins and verifying its checksum, and locates the
  payloads it reads through their ArtifactRecords. It refuses to run over a
  pinned revision whose validation blocked downstream use.
- Scenario mining filters on current-revision readiness.
- `EXPORT_ANALYTICS_SNAPSHOT` writes `scenes`, `observations`, `keyframes`
  and `annotations` tables from verified current revisions. Every timestamp
  column is paired with its `source_clock`; observations carry
  `payload_artifact_id`, not a location.

## 6. Producer: RecordingSceneBuilder

`RECORDING_SCENE_BUILDING` builds the Scenes of exactly one RobotRun and
registers them as the complete set of its recording scope:

```text
build_recording_scenes   BUILD_RECORDING_SCENES (robot_run_id, build_config)
register_scenes          REGISTER_SCENES (manifest_artifact_ids from the build; replace)
validate_scene           VALIDATE_SCENE (scene_ids from registration)
profile_scene            PROFILE_SCENE (optional)
```

The builder (`sceneops_worker.scenes.recording_builder`, producer
`sceneops.recording_scene_builder`, semantics version 1) reads the recording
only through `resolve_recording(robot_run_id)`, requires it to pass the L1
conformance suite, and reads messages through
`sceneops_integrations.recording.reader`. Its behavior is a function of
recording content and `RecordingSceneBuildConfig`
(`sceneops_core.scenes.recording_build`), whose normalized form is
`ProducerInfo.build_config`:

```text
channels       topic (verbatim) · modality · sensor_id? · time policy · payload extraction
               · camera_info_topic (cameras)
time policy    header_stamp (declared clock) | log_time (mcap_log_time)
               | publish_time (mcap_publish_time)
frames         ego_frame_id · world_frame_id?   (channel frames are sensor frames)
calibration    static transform topics (default /tf_static)
poses          TFMessage topic + parent → child frames + time policy
segmentation   fixed_duration { clock, duration_ns }
```

- **Segmentation.** Windows `[origin + k·d, origin + (k+1)·d)` on the
  segmentation clock, with the origin at the earliest included observation.
  A window with observations is one Scene with `unit_key = segment-<k>`.
  Every channel and pose source must be placeable on that clock: its own
  time is in it, or the clock is `mcap_log_time` / `mcap_publish_time`.
- **Observations** keep their canonical timestamp in their channel's clock,
  ordered by timestamp, then MCAP sequence, then per-channel file order;
  `observation_id = <topic slug>-<rank>`.
- **Payloads.** `compressed_image` stores CompressedImage `data` unchanged
  (`image/jpeg`, `image/png`). `ros2_message` stores the recorded CDR message
  bytes (`application/x.ros2-cdr.<package>.msg.<type>`, e.g. PointCloud2).
  Each payload is a write-once `OBSERVATION_PAYLOAD` artifact at
  `{artifact_root}/observation_payloads/{robot_run_id}/{artifact_id}`, owned
  by the RobotRun. Its id derives from (robot_run_id, topic, per-topic file
  position, extraction), so rebuilds with other segmentation reuse it.
- **Calibration** comes from static transforms relative to the ego frame and
  from CameraInfo intrinsics. Both must be constant for the recording.
  Distortion or rectification SceneManifest v1 cannot express fails the
  build.
- **Poses** are the configured source transforms at their own stamps,
  without interpolation or per-observation association.
- **Fail loudly** on a non-conformant recording, a configured topic absent
  from the recording, an unsupported encoding or image format, missing or
  changing calibration, or a build with no Scene.

Retries converge: every payload key, manifest key and artifact id is
deterministic, existing identical bytes and records are reused, and
anything different fails the job. A changed `build_config` gives a new
fingerprint, which the registrar rejects unless `replace` is set.

## 7. API

```text
GET /api/v1/scenes?dataset_id=&dataset_version=&robot_run_id=
GET /api/v1/scenes/{scene_id}
GET /api/v1/scenes/{scene_id}/quality                    # current-revision runs only
GET /api/v1/datasets/{id}/versions/{v}/quality           # dataset-level aggregate
GET /api/v1/datasets/{id}/versions/{v}/scenes/quality    # paginated per-scene quality
GET /api/v1/scenes/{scene_id}/artifacts
```

Scene membership and summaries cannot be written through the API.
