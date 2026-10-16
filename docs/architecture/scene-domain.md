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
SceneManifest        immutable, canonical JSON description of one revision   (Object Storage)
SceneRecord          the unit: id + source binding                           (PostgreSQL)
SceneRevisionRecord  one registered manifest revision + searchable projection (PostgreSQL)
payloads             observation bytes, one OBSERVATION_PAYLOAD ArtifactRecord each (Object Storage)
```

The manifest is authoritative; a revision record is an index over exactly one
registered manifest. A DatasetVersion holds a Scene only through a member that
pins one of its revisions ([Data model](./data-model.md) §3).

## 1. SceneManifest v2

Schema: `sceneops_core.scenes.schemas.manifests` (`schema_version =
"sceneops.scene_manifest/v2"`). Strict: unknown fields are rejected at every
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
  pose, a keyframe group's reference time — declares its own
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
  equal the reference's (`sceneops_worker.recordings.payload_refs`) and dispatches
  on `media_type`.
- **References resolve** (channel frames, calibration channel/frame,
  ego-pose role, group members), and every
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

## 2. Unit and revision records

Schemas: `sceneops_core.scenes.schemas.records`; tables `scenes`,
`scene_revisions` ([Data model](./data-model.md) §3).

```text
SceneRecord (scenes)                 the unit; never updated
  scene_id             scene_id_for(source): unit_id v2 over (source_kind,
                       robot_run_id, unit_key); checked against the binding
  source_kind          recording
  robot_run_id         the source RobotRun; FK → robot_runs (RESTRICT)
  unit_key             <segmentation>/<segment> (§6)
  registered_at

SceneRevisionRecord (scene_revisions)   one registered manifest; never updated
  scene_id, manifest_checksum          the revision
  manifest_artifact_id                 manifest_artifact_id_for(scene_id, checksum);
                                       FK → artifacts (RESTRICT)
  producer_fingerprint                 at most one checksum per (unit, fingerprint)
  window_clock, window_start_timestamp_ns, window_end_timestamp_ns
                                       the segment window [start, end) in window_clock
  observed_channels, observation_count, keyframe_count
  registered_at
  (source_kind, robot_run_id, unit_key: the unit's binding, read from the unit)
```

There is no status column and no "current" revision: a unit has one or more
revisions, and every consumer names the revision it reads: one it was given, or
the one a DatasetVersion member pins. `scene_id` never depends on a
DatasetVersion, storage location, producer configuration, window or revision
([Ingress boundaries](./ingress-boundaries.md) §6), so one Scene is pinned by
any number of DatasetVersions with one manifest object.

The window columns are a projection of the declared segment window, never
of observation extent, and never define identity. SceneOps does not
compute a cross-clock interval for any Scene.

## 3. Registration: the only writer

`REGISTER_SCENES` (`sceneops_worker.scenes.registration`) takes the ids of the
`SCENE_MANIFEST` ArtifactRecords of one build. It is the only writer of Scene
units and revisions. It names no DatasetVersion and never reads or writes
members or summaries: a DatasetVersion pins the revisions registration
returns (ADR-015 §7).

```text
gate       manifest  each artifact is a SCENE_MANIFEST whose id is the revision's
                     deterministic id; bytes read back from the store match its
                     size + checksum; canonical strict parse
           payloads  every PayloadRef resolves to an OBSERVATION_PAYLOAD
                     ArtifactRecord with exactly its checksum, size_bytes and
                     media_type, and that record's object is present in the store
                     with its recorded size (one listing per payload prefix)
scope      the input is one build: one RobotRun, one producer fingerprint, built
           from that RobotRun's registered recording bytes
revisions  one transaction, units in unit id order; each unit and revision
           inserted if absent (decide_revision_registration): identical → no-op ·
           new unit or new fingerprint → insert · same fingerprint, other
           checksum → conflict (non-deterministic producer); commit
result     the complete pin (domain, unit_id, manifest_artifact_id,
           manifest_checksum) of every input revision, with its decision
```

A revision row therefore means "registered, and its bytes were present and
checksum-consistent in the store at registration"; a foreign key to it proves
registration, and the gate proves the bytes. Gate and scope failures happen
before the transaction and write nothing; any failure in it rolls the whole
input back, so units are never skipped. No lock is taken: the unit and revision
unique keys decide concurrent registrations, which converge when identical and
fail when they conflict, and inserting in unit id order keeps overlapping
registrations from deadlocking. Registration never removes or repoints a
revision. A recording build that yields no units fails.

Producers own their bytes: a producer writes a manifest with
`SceneArtifactStore.publish_canonical_manifest` (write-once, unit-scoped,
checksum-qualified key, see [Storage layout](./storage-layout.md)) and registers
its `SCENE_MANIFEST` ArtifactRecord under `manifest_artifact_id_for(scene_id,
checksum)`, owned by the Scene and carrying no DatasetVersion, with its own
execution lineage, after registering the `OBSERVATION_PAYLOAD` ArtifactRecords
its manifest references. Registration never writes manifest or payload bytes or
ArtifactRecords.

## 4. Quality and readiness

`VALIDATE_SCENE` / `PROFILE_SCENE` take a DatasetVersion and `scene_ids`,
read each Scene at the revision that version's member pins
(`sceneops_worker.scenes.resolver.resolve_member_scene`; without a
DatasetVersion they fail), and write a per-scene run record that pins that
revision (`manifest_artifact_id` + `manifest_checksum`). They never write a
unit, revision, member or DatasetVersion state.
Validation reports per-channel aggregates: required channels without
observations (blocking), declared channels without observations, keyframes
missing a required channel (optionally blocking), and missing calibration,
intrinsics, image size or ego poses.

Readiness (`sceneops_core.scenes.readiness`) is derived from the newest
succeeded validation run **of the revision being assessed**; runs of any
other revision are ignored, so a newly pinned revision is `unknown` until it
is validated. See [Quality and run records](./quality-and-runs.md)
§2.

## 5. Derived consumers

Everything downstream of registration reads canonical Scenes through a pin:

- `BUILD_SCENE_SAMPLE_VIEWS` derives a `SceneSampleView` from one pinned
  Scene revision and an explicit policy (anchor channel, nearest / previous
  association with tolerances, ego pose, label revisions). The Scene stays
  asynchronous; synchronization exists only in the view. Each view pins the
  Scene `manifest_artifact_id` / `manifest_checksum`. `IMPORT_LABELS`
  produces independent label set revisions anchored on canonical
  observations; labels are never written into a Scene.
- Scenario mining, detection and evaluation consume sample views and label
  sets through checksum-verified pins (`sceneops_derived`), locate
  payloads through their ArtifactRecords, and refuse a view whose Scene
  revision blocked downstream use. See
  [Derived layer](./derived-layer.md).
- `EXPORT_ANALYTICS_SNAPSHOT` writes `scenes`, `observations` and `keyframes`
  tables from the verified revisions the DatasetVersion pins. Every timestamp
  column is paired with its `source_clock`; observations carry
  `payload_artifact_id`, not a location.

## 6. Producer: RecordingSceneBuilder

`RECORDING_SCENE_BUILDING` builds the Scenes of exactly one RobotRun and
registers them as revisions:

```text
build_recording_scenes   BUILD_RECORDING_SCENES (robot_run_id, build_config)
register_scenes          REGISTER_SCENES (manifest_artifact_ids from the build)
                         → scene_ids, revisions (complete pins)
validate_scene           VALIDATE_SCENE (scene_ids from registration)
profile_scene            PROFILE_SCENE (optional)
```

The builder (`sceneops_scenes.recording_builder`, producer
`sceneops.recording_scene_builder`, semantics version 3) reads the recording
only through `resolve_recording(robot_run_id)`, requires it to pass the L1
conformance suite, and reads messages through
`sceneops_recording.reader`. Its behavior is a function of
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
segmentation   whole_recording { clock } | fixed_duration { clock, duration_ns }   (policy is explicit)
```

- **Segmentation.** The policy is explicit, and each policy is a half-open
  window on one declared segmentation clock:

  ```text
  whole_recording  [earliest, latest + 1) over every included observation
                   and pose                                   segment recording
  fixed_duration   [k·d, (k+1)·d): a grid anchored at the clock's zero;
                   non-empty windows                          segment <k>
  ```

  Segmentation only groups: observations, poses, calibration and payload
  identities are the same under either policy, and the policy is part of the
  producer fingerprint.
- **Unit keys carry the segmentation** (ADR-015 §6.3,
  `sceneops_core.provenance.segmentation`):
  `unit_key = <policy>-<sha256(boundary document)[:16]>/<segment>`, where the
  boundary document is `RecordingSceneBuildConfig.segmentation_boundary()`
  (policy, clock, duration) and nothing else. Two segmentations of one RobotRun
  therefore yield different units, while another channel or pose selection
  under the same segmentation is a new revision of the same units: the
  fixed-duration grid does not move with the selection. A `whole_recording`
  window is the extent of the included data, so it may differ between
  revisions of its one unit. Every channel and pose source must be placeable on the
  segmentation clock: its own time is in it, or the clock is `mcap_log_time` /
  `mcap_publish_time`.
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
  Distortion or rectification SceneManifest v2 cannot express fails the
  build.
- **Poses** are the configured source transforms at their own stamps,
  without interpolation or per-observation association.
- **Fail loudly** on a non-conformant recording, a configured topic absent
  from the recording, an unsupported encoding or image format, missing or
  changing calibration, or a build with no Scene.

Retries converge: every payload key, manifest key and artifact id is
deterministic, existing identical bytes and records are reused, and
anything different fails the job. A changed `build_config` gives a new
fingerprint: a new revision of the same units (or, for another segmentation,
other units), which the registrar adds beside the registered ones. No
DatasetVersion changes.

## 7. API

```text
GET /api/v1/scenes?robot_run_id=                         # units
GET /api/v1/scenes/{scene_id}                            # the unit and every revision
GET /api/v1/scenes/{scene_id}/quality?dataset_id=&dataset_version=
                                                         # the revision the version pins
GET /api/v1/datasets/{id}/versions/{v}/scenes            # the revisions the version pins
GET /api/v1/datasets/{id}/versions/{v}/quality           # dataset-level aggregate
GET /api/v1/datasets/{id}/versions/{v}/scenes/quality    # paginated per-scene quality
GET /api/v1/scenes/{scene_id}/artifacts
```

Scene units, revisions, members and summaries cannot be written through the
API.
