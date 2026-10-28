# Scene Domain

A **Scene** is SceneOps' canonical spatiotemporal environmental observation
unit: within a selected boundary (a window of a registered RobotRun
recording, or one native scene of a registered SourceContent, over a
configured set of channels) it keeps every source observation with its own
source timing, verbatim source channel identity, payload, calibration,
coordinate-frame semantics and source poses, together with source and producer
provenance. Scenes are produced from registered recordings
([ADR-007](../adr/007-canonical-ingestion-architecture.md) §29, §30) and from
registered nuScenes contents ([ADR-015](../adr/015-multi-source-platform.md) §6),
by one builder per source format; no RobotRun is involved in the second.

Three things make up a Scene ([ADR-007](../adr/007-canonical-ingestion-architecture.md) §3):

```text
SceneManifest        immutable, canonical JSON description of one revision   (Object Storage)
SceneRecord          the unit: id + source binding                           (PostgreSQL)
SceneRevisionRecord  one registered manifest revision + searchable projection (PostgreSQL)
payloads             recording-derived observation bytes, one OBSERVATION_PAYLOAD
                     ArtifactRecord each, or preserved files of the unit's own
                     content, referenced in place (Object Storage)
```

The manifest is authoritative; a revision record is an index over exactly one
registered manifest. A DatasetVersion holds a Scene only through a member that
pins one of its revisions ([Data model](./data-model.md) §3).

## 1. SceneManifest v4

Schema: `sceneops_core.scenes.schemas.manifests` (`schema_version =
"sceneops.scene_manifest/v4"`). One schema serves both source kinds; the
loader reads v4 only. Strict: unknown fields are rejected at every
level, and there is no free-form metadata. A source fact the schema cannot
express is a contract amendment.

```text
SceneManifest
  schema_version
  lineage              source: SourceBinding (Ingress boundaries §4)
                         RecordingSegmentSource (robot_run_id, recording_artifact_id,
                           recording_checksum, source_clock, [start, end), unit_key)
                         | ContentUnitSource (content_id, content_checksum,
                           source_clock, [start, end), unit_key)
                       producer: ProducerInfo (fingerprint re-derived on parse)
  clock_domains[]      every clock the Scene uses, once: clock, origin,
                       resolution_ns (Ingress boundaries §4.1)
  coordinate_frames[]  frame_id (verbatim) + role: world | ego | sensor
  channels[]           channel (verbatim source identity), modality
                       (camera | lidar | radar | other), frame_id,
                       source_clock, sensor_id?
  calibrations[]       per channel: extrinsic (channel frame in a parent frame),
                       camera_intrinsic?
  observations[]       observation_id, channel, timestamp_ns, payload (PayloadRef),
                       calibration_id?, ego_pose_id?, image_size?, native_ref?
  poses[]              timestamped source transforms (e.g. ego poses),
                       each with its source_clock
  groups[]             source-defined groupings: kind = keyframe, timestamp_ns,
                       source_clock, observation_ids (at most one per channel),
                       native_ref?
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
  different clocks; each member keeps its own. Every clock named by the
  window, a channel, a pose or a group is declared once in `clock_domains`,
  and nothing else is declared. A recording Scene declares every clock with
  origin `source`; a nuScenes Scene declares `nuscenes.timestamp` with origin
  `unix_epoch` ([Ingress boundaries](./ingress-boundaries.md) §4.1).
- **Boundaries.** A Scene's boundary is its source unit's half-open window
  `[start, end)` in the source clock: for a recording, the segmentation clock
  the producer's build configuration declares (`mcap_log_time`,
  `mcap_publish_time` or a declared source clock); for a content, the clock of
  its native unit (§7). Every timestamp counted in that clock must lie inside
  it, and timestamps in other clocks are not compared with it. The window is
  never derived from the RobotRun's `started_at` / `ended_at`.
- **Source identity is verbatim.** Channels, frames and categories are the
  source's own names; `modality`, frame `role` and `sensor_id` are canonical
  semantics added alongside them, never instead.
- **Conventions are fixed** by the schema version and carried in field
  names: translations in metres (`*_m`), unit quaternions `[w, x, y, z]`
  (`rotation_wxyz`), box size `[width, length, height]` (`size_wlh_m`), and a
  `FrameTransform` maps points in `child_frame_id` into `parent_frame_id`.
- **Payloads** are strict `PayloadRef`s (`sceneops_core.artifacts.schemas.payload`),
  a union discriminated by `kind`, each with a `media_type` that fully
  specifies encoding and layout and that consumers dispatch on:

  ```text
  ArtifactPayloadRef  kind "artifact": artifact_id, checksum, size_bytes; names the
                      OBSERVATION_PAYLOAD ArtifactRecord (recording-derived payloads)
                      artifact_id -> ArtifactRecord -> uri -> ArtifactStore
  SourceFileRef       kind "source_file": content_id, path, sha256, size_bytes,
                      frame (null for a whole file); names one preserved file of the
                      Scene's own content, as its inventory pins it
                      (content_id, path) -> inventory entry -> content files -> ArtifactStore
  ```

  Neither names a location, so moving bytes updates ArtifactRecords and never
  changes a manifest or its checksum. A `SourceFileRef` must name the Scene's
  own content: a recording Scene, or a reference to another content, is
  rejected. The one place downstream of manifests that dispatches on the kind
  is the payload locator (`sceneops_worker.payloads.PayloadLocator`), which
  requires the ArtifactRecord, or the inventory entry, to equal the reference.
- **Native references.** `native_ref` is the source's own identifier of an
  observation or a group, verbatim (a nuScenes `sample_data.token` /
  `sample.token`); it occurs at most once among observations and once among
  groups. A recording Scene has none.
- **References resolve** (channel frames, calibration channel/frame,
  ego-pose role, group members), and every
  list has one canonical order, with unique ids. Timestamped lists other than
  observations are ordered by `(source_clock, timestamp_ns, id)`, so the
  order never interleaves clocks.
- **No membership.** A manifest has no `scene_id`, DatasetVersion, status or
  execution context, so identical source + producer + build configuration
  produce byte-identical manifests in any DatasetVersion.
- **No location.** Provenance names the RobotRun or the SourceContent and pins
  its bytes by checksum; it carries no URI. Acquisition-origin metadata inside
  a recording, and the SourceSnapshot, upstream, revision and licence of a
  content's declaration, never reach a manifest.

Bytes go through `SceneManifest.to_canonical_bytes()` (the shared canonical
JSON serializer) and `load_canonical_scene_manifest()`, which rejects bytes
that parse but are not already canonical.

## 2. Unit and revision records

Schemas: `sceneops_core.scenes.schemas.records`; tables `scenes`,
`scene_revisions` ([Data model](./data-model.md) §3).

```text
SceneRecord (scenes)                 the unit; never updated
  scene_id             scene_id_for(source): unit_id v2 over (source_kind,
                       source_id, unit_key); checked against the binding
  source_kind          recording | content
  robot_run_id         the source RobotRun (recording); FK → robot_runs (RESTRICT)
  content_id           the source SourceContent (content); FK → source_contents
                       (RESTRICT); exactly one of the two is set
  unit_key             <segmentation>/<segment> (§6) | scene.token (§7)
  registered_at

SceneRevisionRecord (scene_revisions)   one registered manifest; never updated
  scene_id, manifest_checksum          the revision
  manifest_artifact_id                 manifest_artifact_id_for(scene_id, checksum);
                                       FK → artifacts (RESTRICT)
  producer_fingerprint                 at most one checksum per (unit, fingerprint)
  window_clock, window_start_timestamp_ns, window_end_timestamp_ns
                                       the unit window [start, end) in window_clock
  observed_channels, observation_count, keyframe_count
  registered_at
  (source_kind, robot_run_id | content_id, unit_key: the unit's binding, read
   from the unit)
```

There is no status column and no "current" revision: a unit has one or more
revisions, and every consumer names the revision it reads: one it was given, or
the one a DatasetVersion member pins. `scene_id` never depends on a
DatasetVersion, storage location, producer configuration, window or revision
([Ingress boundaries](./ingress-boundaries.md) §6), so one Scene is pinned by
any number of DatasetVersions with one manifest object.

The window columns are a projection of the declared unit window, never
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
           payloads  by kind: an ArtifactPayloadRef resolves to an
                     OBSERVATION_PAYLOAD ArtifactRecord with exactly its checksum,
                     size_bytes and media_type, whose object is present in the store
                     with its recorded size (one listing per payload prefix); a
                     SourceFileRef names a path of the registered content's
                     inventory with exactly its sha256 and size, whose object is
                     present with that size (one listing per content prefix)
scope      the input is one build: one source (a RobotRun or a SourceContent),
           one producer fingerprint, built from that source's registered bytes
           (the recording checksum, or the content checksum)
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
revision. A build that yields no units fails.

Producers own their bytes: a producer writes a manifest with
`SceneArtifactStore.publish_canonical_manifest` (write-once, unit-scoped,
checksum-qualified key, see [Storage layout](./storage-layout.md)) and registers
its `SCENE_MANIFEST` ArtifactRecord under `manifest_artifact_id_for(scene_id,
checksum)`, owned by the Scene and carrying no DatasetVersion, with its own
execution lineage, after registering the `OBSERVATION_PAYLOAD` ArtifactRecords
its manifest references. Registration never writes manifest or payload bytes or
ArtifactRecords.

## 4. Quality and readiness

`VALIDATE_SCENE` / `PROFILE_SCENE` take `revisions`: complete Scene revision
pins, at most one per unit. They read each pinned revision exactly
(`sceneops_worker.scenes.resolver.resolve_scene_revisions`; a pin that names no
registered revision fails the job before anything is assessed) and write a
per-scene run record that pins that revision (`manifest_artifact_id` +
`manifest_checksum`). They read no DatasetVersion: a revision is assessed once,
however many versions pin it. They never write a unit, revision, member or
DatasetVersion state. Unit ids without a pin (`scene_ids`) and DatasetVersion
fields are refused.
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
  Scene `manifest_artifact_id` / `manifest_checksum`. It reads observations
  only, so it builds views of either source kind alike. `IMPORT_LABELS`
  produces independent label set revisions anchored on canonical
  observations of a RobotRun; labels are never written into a Scene. A label
  anchors on its RobotRun, so no label resolves into a content-sourced Scene
  ([Limitations](./limitations.md) §6).
- Scenario mining, detection and evaluation consume sample views and label
  sets through checksum-verified pins (`sceneops_derived`), locate
  payloads through the payload locator (an ArtifactRecord, or a content
  inventory entry), and refuse a view whose Scene revision blocked downstream
  use. See [Derived layer](./derived-layer.md).
- `EXPORT_ANALYTICS_SNAPSHOT` writes `scenes`, `observations` and `keyframes`
  tables from the verified revisions the DatasetVersion pins. Every timestamp
  column is paired with its `source_clock`; observations carry
  `payload_artifact_id`, not a location. A Scene whose observations reference
  preserved source files is refused explicitly (`UnsupportedSceneContentError`).

## 6. Producer: RecordingSceneBuilder

`RECORDING_SCENE_BUILDING` builds the Scenes of exactly one RobotRun and
registers them as revisions:

```text
build_recording_scenes   BUILD_RECORDING_SCENES (robot_run_id, build_config)
                         → manifest_artifact_ids
register_scenes          REGISTER_SCENES (manifest_artifact_ids from the build)
                         → scene_ids, revisions (complete pins)
compose_dataset_version  COMPOSE_DATASET_VERSION (optional: the run names
                         {dataset_id, dataset_version}; pins = revisions)
validate_scene           VALIDATE_SCENE (revisions from registration)
profile_scene            PROFILE_SCENE (optional; revisions from registration)
```

The builder (`sceneops_scenes.recording_builder`, producer
`sceneops.recording_scene_builder`, semantics version 5) reads the recording
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
  bytes (`application/x.ros2-cdr.<package>.msg.<type, lowercased>`, e.g.
  `application/x.ros2-cdr.sensor_msgs.msg.pointcloud2`).
  Each payload is a write-once `OBSERVATION_PAYLOAD` artifact at
  `{artifact_root}/observation_payloads/{robot_run_id}/{artifact_id}`, owned
  by the RobotRun. Its id derives from (robot_run_id, topic, per-topic file
  position, extraction), so rebuilds with other segmentation reuse it.
- **Calibration** comes from static transforms relative to the ego frame and
  from CameraInfo intrinsics. Both must be constant for the recording.
  Distortion or rectification SceneManifest v4 cannot express fails the
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

## 7. Producer: nuScenes Scene builder

`SNAPSHOT_SCENE_BUILDING` builds the Scenes of the content of one registered
SourceSnapshot and registers them as revisions; the stages after the build are
those of §6:

```text
build_snapshot_scenes    BUILD_SNAPSHOT_SCENES (snapshot_id, build_config, unit_keys?)
                         → snapshot_id, content_id, content_checksum, manifest_artifact_ids
register_scenes / compose_dataset_version / validate_scene / profile_scene   (as §6)
```

The build resolves the snapshot to its content once and records both in its Job
result: which declaration a build used is execution provenance, and the manifest
ArtifactRecords keep the Job that first produced them. It builds from the
content alone, so every snapshot of the same bytes builds the same revisions.
`unit_keys` is execution scope: it never enters the fingerprint, a manifest
depends only on its own scene, so a subset builds the same revisions, and an
unknown key fails. DatasetVersion fields are rejected.

The builder (`sceneops_scenes.nuscenes_builder`, producer
`sceneops.nuscenes_scene_builder`, semantics version 1) reads the content only
through the native reader `sceneops_sources.nuscenes`, which parses the nuScenes
v1.0 tables (`scene`, `sample`, `sample_data`, `ego_pose`, `calibrated_sensor`,
`sensor` of the one `v1.0-<split>` table directory) through verified content
files, checks that every token reference between them resolves and refuses any
other table version. Sensor files, CAN, maps and the annotation tables are
preserved in the content and never read. `NuScenesSceneBuildConfig`
(`sceneops_core.scenes.nuscenes_build`) selects the channels; everything else is
the source's:

```text
unit           one Scene per native scene; unit_key = scene.token (scene.name is
               display data and enters nothing)
readings       every sample_data whose sample belongs to the scene (key frames and
               sweeps), of the selected channels
clock          nuscenes.timestamp, origin unix_epoch, resolution 1 000 ns;
               t_ns = timestamp_us · 1 000, exact, for readings, ego poses and samples
window         [first, last + 1 ns) over every reading the source assigns to the
               scene's samples, on every channel, selected or not
channels       sensor.channel verbatim; modality = sensor.modality (camera | lidar |
               radar); frame = the channel name
frames         global (world: ego poses are given in it), ego (ego: calibrations are
               given in it), one sensor frame per selected channel
calibrations   cal-<calibrated_sensor.token> per calibration a reading references:
               ego → channel frame, translation and rotation [w, x, y, z] verbatim;
               camera_intrinsic for a camera, none for lidar / radar
ego poses      pose-<ego_pose.token> per reading: global → ego at ego_pose.timestamp
observations   <channel>-<rank, 8 digits>, rank in (timestamp, token) order per channel;
               payload = SourceFileRef of the reading's preserved file with the
               inventory's sha256 and size; calibration_id and ego_pose_id from the
               reading's own tokens; image size for a camera;
               native_ref = sample_data.token
media types    from the modality, the file suffix and the table's fileformat:
               camera .jpg image/jpeg · lidar .pcd.bin
               application/x.nuscenes.lidar-pcd-bin · radar .pcd
               application/x.nuscenes.radar-pcd
keyframes      one group per sample: keyframe-<rank, 6 digits> in (timestamp, token)
               order, at sample.timestamp, the key-frame reading of each selected
               channel; native_ref = sample.token
```

No payload object is written: every reading stays the preserved file it is.
A selected scene with a referenced file that is not in the content inventory
fails the build, naming the scene; it is never skipped, so building a slice
snapshot without `unit_keys` fails on the first scene the slice does not
preserve. Evidence that disagrees (a camera reading that is not a `.jpg` of
fileformat `jpg`, a modality other than camera / lidar / radar, a camera
without intrinsics, an intrinsic on a lidar or radar, two key frames of one
channel in one sample, a sample with no key frame on a selected channel), a
non-unit quaternion and any manifest invariant the source breaks fail the
build.

## 8. API

```text
GET /api/v1/scenes?robot_run_id=|content_id=             # units, optionally of one source
GET /api/v1/scenes/{scene_id}                            # the unit and every revision
GET /api/v1/scenes/{scene_id}/quality?manifest_checksum=
                                                         # quality of one revision, or
GET /api/v1/scenes/{scene_id}/quality?dataset_id=&dataset_version=
                                                         # of the revision the version pins
GET /api/v1/datasets/{id}/versions/{v}/scenes            # the revisions the version pins
GET /api/v1/datasets/{id}/versions/{v}/quality           # dataset-level aggregate
GET /api/v1/datasets/{id}/versions/{v}/scenes/quality    # paginated per-scene quality
GET /api/v1/scenes/{scene_id}/artifacts
```

Scene units, revisions, members and summaries cannot be written through the
API.
