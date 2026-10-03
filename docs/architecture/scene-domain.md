# Scene Domain

A **Scene** is SceneOps' canonical spatiotemporal environmental observation
unit: within a selected boundary (an external source unit, or a window of a
robot recording, over a configured set of source channels) it keeps every source
observation with its own source timing, verbatim source channel identity,
payload, calibration, coordinate-frame semantics, source poses and source
annotations, together with source and producer provenance.

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
  lineage              source: UnitSource (ExternalUnitSource | RecordingSegmentSource)
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
- **Boundaries.** An external Scene's boundary is its source unit
  (`source_unit_key`); it has no time window, and its observations' extent
  is never promoted to one. A recording-derived Scene's boundary is its
  segment's half-open window `[start, end)` in the segment's clock: every
  timestamp counted in that clock must lie inside it, and timestamps in
  other clocks are not compared with it.
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
- **No location or display name.** External provenance is the source
  revision (`ExternalSourceRevision`) and the `source_unit_key`. The
  integration's `ExternalDatasetRef.uri` and `external_name` locate and label
  the source while it is read and are dropped on canonicalization
  (`ExternalUnitSource.from_ref`), so the same source revision read from any
  path or URI, under any name, yields the same manifest bytes.

Bytes go through `SceneManifest.to_canonical_bytes()` (the shared canonical
JSON serializer) and `load_canonical_scene_manifest()`, which rejects bytes
that parse but are not already canonical.

## 2. SceneRecord

Schema: `sceneops_core.scenes.schemas.records`; table `scenes`.

```text
scene_id               deterministic: f(dataset_id, dataset_version, "scene", source identity)
dataset_id, dataset_version     FK → dataset_versions (RESTRICT)
source_kind            external | recording
external_format        set iff external
robot_run_id           set iff recording; FK → robot_runs (RESTRICT)
source_unit_key        external source unit key, or recording unit key
producer_fingerprint
manifest_artifact_id   FK → artifacts (RESTRICT): the current revision
manifest_checksum
window_clock, window_start_timestamp_ns, window_end_timestamp_ns
                       the source's declared window [start, end) in window_clock:
                       set for a recording segment, all NULL for an external Scene
observed_channels, observation_count, keyframe_count, annotation_count
registered_at, updated_at
```

There is no status column. A record exists if and only if the unit is
registered; its current revision is exactly `manifest_artifact_id`, never
"the latest manifest". `scene_id` comes from
`sceneops_core.provenance.canonical_unit_id`: a hash of a canonical identity
document, so it never depends on storage location, producer configuration
or revision. The same source unit in two DatasetVersions is two Scenes.

The window columns are a projection of a boundary the source itself
declares, never of observation timestamps, and never define identity.
SceneOps does not compute a cross-clock or observation-extent interval for
any Scene.

## 3. Registration: the only writer

`REGISTER_SCENES` (`sceneops_worker.scenes.registration`) takes a
DatasetVersion and the ids of `SCENE_MANIFEST` ArtifactRecords. It is the
only writer of `scenes` rows and of the DatasetVersion Scene summary.

```text
verify   each artifact is a SCENE_MANIFEST; bytes match its size + checksum;
         canonical strict parse; every PayloadRef resolves to an
         OBSERVATION_PAYLOAD ArtifactRecord with exactly its checksum,
         size_bytes and media_type
scope    one source kind per registration; a recording registration covers one
         RobotRun, carries one producer fingerprint, and must have been built
         from that RobotRun's registered recording bytes and clock
apply    one transaction under the DatasetVersion row lock:
           external unit      absent → insert · same fingerprint and checksum → no-op
                              · different → conflict, or repoint with replace=True
           recording scope    empty → insert · same fingerprint → no-op (whole scope)
                              · different → conflict, or replace=True: delete ids
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

## 6. Legacy Scene producers

The nuScenes scene integration (`DATASET_SCENE_INGESTION` → `INGEST_SCENES`)
and the raw-log builder (`RAW_LOG_SCENE_BUILDING` → `BUILD_SCENES`) still
produce pre-canonical, sample-centric manifests
(`sceneops_core.scenes.legacy`) with payload paths relative to an external
source root and keyframe-only or sampling-selected frames. Their output is
stored as `LEGACY_SCENE_MANIFEST` artifacts under `legacy_scenes/`, their
pipelines end at the producer task, and canonical registration rejects their
manifests. Neither writes Scene membership or DatasetVersion state.

## 7. API

```text
GET /api/v1/scenes?dataset_id=&dataset_version=&source_kind=&external_format=&robot_run_id=
GET /api/v1/scenes/{scene_id}
GET /api/v1/scenes/{scene_id}/quality                    # current-revision runs only
GET /api/v1/datasets/{id}/versions/{v}/quality           # dataset-level aggregate
GET /api/v1/datasets/{id}/versions/{v}/scenes/quality    # paginated per-scene quality
GET /api/v1/scenes/{scene_id}/artifacts
```

Scene membership and summaries cannot be written through the API.
