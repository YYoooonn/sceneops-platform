# Episode Domain

An `Episode` is a canonical, source-faithful, task/behavior-oriented
projection of one source unit: a window of a registered RobotRun recording, or
one native episode of a registered SourceContent (a preserved LeRobot v3.0
dataset). It keeps the observation, state, action, per-frame transition and
task/event streams of the unit, each at its own timestamps on its own declared
clock. Where a Scene answers "what did the sensors see in this window," an
Episode answers "what did the robot observe, report and do during this task."

Scene and Episode are siblings over the same RobotRun: each is built
directly from the recording, neither reads the other, and either may be
built first. The authoritative contracts are ADR-007 §31 (Amendment A6) and
ADR-015 §6.

**Canonical Episode preserves its source streams. Temporal alignment is a
derived (L3) operation** (`ALIGN_EPISODE` → `AlignedEpisode`, the first stage
of the `EPISODE_LEARNING_DATA_BUILDING` pipeline; see
[Robot learning data layer](./robot-learning-data.md)).

## 1. Pipelines

There is one builder per source format; both pipelines share registration,
composition, validation and profiling.

```text
RECORDING_EPISODE_BUILDING  (one robot_run_id per run, no DatasetVersion scope)
  build_recording_episodes -> register_episodes -> compose_dataset_version (optional)
                                                -> validate_episode
                                                -> profile_episode (optional)
SNAPSHOT_EPISODE_BUILDING   (one snapshot_id per run, no DatasetVersion scope)
  build_snapshot_episodes  -> register_episodes -> (as above)
```

```text
build_recording_episodes   resolve_recording(robot_run_id) -> L1 conformance check
                           -> plan_recording_episodes (pure) -> OBSERVATION_PAYLOAD
                           artifacts -> EPISODE_MANIFEST artifacts (write-once)
build_snapshot_episodes    the registered SourceSnapshot -> its content_id, once
                           -> the Episode builder of the content's registered format
                           -> the files its reader reads, verified against the
                           inventory -> plan_lerobot_episodes (pure)
                           -> EPISODE_MANIFEST artifacts (write-once); no payload
register_episodes          the only writer of Episode units and revisions;
                           returns their complete pins (revisions), writes no
                           DatasetVersion state
compose_dataset_version    optional: exactly those pins, into the version the
                           run names ({dataset_id, dataset_version})
validate_episode /         the pinned revisions; run records pinned to the
profile_episode            revision they assessed
```

The recording build takes `robot_run_id` and `build_config` only; recording
URIs, paths, robot ids and DatasetVersion fields are rejected. The snapshot
build takes `snapshot_id`, `build_config` and optional `unit_keys`;
DatasetVersion fields are rejected. `unit_keys` is execution scope: it never
enters the fingerprint, a manifest depends only on its own unit, so a subset
builds the same revisions, and an unknown key fails the build. The snapshot a
build used is execution provenance: its Job result records `snapshot_id`,
`content_id` and `content_checksum`, and the manifest ArtifactRecords keep the
Job that first produced them. No manifest names a snapshot. A DatasetVersion
spanning several sources is one `COMPOSE_DATASET_VERSION` of the pins their
builds registered ([Jobs and pipelines](./jobs-and-pipelines.md) §2).

## 2. Build configuration

Every semantic decision is in the build configuration; its normalized form is
the producer's `ProducerInfo.build_config` and part of the fingerprint.

### 2.1 Recordings

`RecordingEpisodeBuildConfig`
(`packages/sceneops-core/sceneops_core/episodes/recording_build.py`):

```text
streams[]     topic (verbatim) · role: observation | state | action
              · time: header_stamp | log_time | publish_time | payload_field{field}
                      + clock identifier
              · decoding: ros2 | json_string (JSON object in std_msgs/String)
              · fields[] {name, path}     selected values, kept as recorded
              · payload (observation only): compressed_image | ros2_message
events[]      task / event sources: topic · time · decoding · fields[]
segmentation  whole_recording {clock}
              fixed_duration {clock, duration_ns}
              event_markers {event_topic, key_field, state_field,
                             start_values[], end_values[]}
```

No topic, field name or action schema is part of the Episode model. A
vehicle's `steering` / `throttle` / `brake`, a robot's joint commands or a
`/mission/status` task topic are configuration. Example (the step-6
acquisition output):

```text
/camera/front/image/compressed  observation  header_stamp  payload compressed_image
/vehicle/odom                   state        header_stamp  pose.pose.position.x, ...
/vehicle/status                 state        header_stamp  percentage
/vehicle/control                action       payload_field source_timestamp_ns (json_string)
/mission/status                 event        payload_field source_timestamp_ns (json_string)
segmentation  event_markers on /mission/status: mission_id, running -> completed
```

### 2.2 LeRobot content

`LeRobotEpisodeBuildConfig`
(`packages/sceneops-core/sceneops_core/episodes/lerobot_build.py`):

```text
features[]    feature key (verbatim) · role: observation | state | action | transition
task_events   keep the per-frame task as task_index events (default true)
```

The unit boundary, clock and time rule are the source's, not configuration.
The structural features `timestamp`, `frame_index`, `episode_index`, `index`
and `task_index` cannot be selected: they are represented by time, the unit
key, `native_ref` and task events. The reference configuration for pusht is
`config/reference/lerobot-pusht/episode_build_config.json` (image as
observation, `observation.state` as state, `action`, and `next.reward`,
`next.done`, `next.success` as transitions).

## 3. Units and windows

### 3.1 Recordings

An Episode window is a half-open interval `[start, end)` in one
segmentation clock (`RecordingSegmentSource.source_clock`): the clock the
segmentation declares, or for `event_markers` the event source's clock. Every
stream must be placeable on it (its time is in that clock, or the clock is
`mcap_log_time` / `mcap_publish_time`). Streams keep their own clocks;
timestamps in another clock are never compared with the window or converted.

```text
whole_recording  [earliest, latest + 1)                          segment recording
fixed_duration   [k·d, (k+1)·d): grid at the clock's zero, non-empty   segment <k>
event_markers    [start marker, end marker + 1) per task key      segment task-<key>-<NNN>
```

`<NNN>` is the 0-based count of that task key's earlier windows in marker order,
zero-padded to three digits (`task-mission-a-000`, `task-mission-a-001`).

The unit key carries the segmentation (ADR-015 §6.3,
`sceneops_core.provenance.segmentation`):
`unit_key = <policy>-<sha256(boundary document)[:16]>/<segment>`. The boundary
document is `RecordingEpisodeBuildConfig.segmentation_boundary()`: the policy,
clock and duration, or for `event_markers` the marker topic, its time policy
(`source`, `clock`, `field`, null when unused) and decoding, the field *paths*
behind `key_field` / `state_field` (`key_path`, `state_path`), and the sorted,
unique start / end values. Another segmentation is other units; another stream or
field selection under the same segmentation is a new revision of the same
units.

Unpaired markers (an end without a start, a second start, a start never
ended) and a build that yields no Episode fail the job. Windows are never
derived from `RobotRun.started_at` / `ended_at`.

### 3.2 LeRobot content

The LeRobot builder (`sceneops_episodes.lerobot_builder`) reads the content
through the native reader `sceneops_sources.lerobot`, which parses LeRobot
v3.0 (`meta/info.json`, `meta/tasks.parquet`, `meta/episodes/**`, data
Parquet) through verified content files and refuses any other
`codebase_version`. Its rules (semantics version 2):

```text
unit          one Episode per native episode; unit_key = str(episode_index)
clock         lerobot.frame_time, origin unit, resolution 1 ns
time          frame k at round_half_even(k · 10⁹ / fps), exact integer arithmetic;
              fps must be a positive integer, frame_index dense from 0 in row
              order, and every recorded timestamp exactly float32(frame_index / fps),
              or the build fails (a dataset off its grid is never snapped)
window        [0, t(length − 1) + 1); frame_timeline {lerobot.frame_time, fps, length}
streams       one per selected feature: topic = feature key, schema_name = its dtype,
              one occurrence per frame, occurrence_id <feature>-<k, 8 digits>,
              native_ref = the frame's global index
values        numeric features as one field `value`; float32 kept as its exact
              float64 widening; a vector declares component_names, the source's
              names in order (a list, or a dict with exactly one key)
video         an observation whose occurrences are PayloadRefs (video/mp4, from the
              video dtype and the .mp4 suffix) to the episode's preserved video file in
              place (sources Logical URI), with a video_frame selector
              round_half_even((from_timestamp + k / fps) · 10⁹) in that file's time
              base; nothing is decoded and no payload is copied
task events   stream task_index: one event at every frame whose task differs from the
              previous frame's (always frame 0), values {task, task_index}
transitions   a selected next.* feature: one occurrence per frame at the frame the row
              records it on, value verbatim; no event is derived from it
```

A unit whose referenced files are not in the content inventory, a selected
feature the dataset lacks, a video feature in another role or with other
format evidence, and a `names` dict with more than one group fail the build.

## 4. EpisodeManifest

`sceneops.episode_manifest/v4`
(`packages/sceneops-core/sceneops_core/episodes/schemas/manifests.py`); the
loader refuses every other version
([ADR-017](../adr/017-unified-payload-ref-and-versioned-manifests.md)):

```text
lineage         source binding: RecordingSegmentSource (robot_run_id, recording
                artifact + checksum, window, unit_key) or ContentUnitSource
                (content_id, content_checksum, window, unit_key)
                + ProducerInfo (fingerprint re-derivable from the binding)
clock_domains[] every clock the Episode uses, once: clock, origin, resolution_ns
frame_timeline? {clock, fps, frame_count} for a unit sampled on a frame grid
streams[]       topic, role (observation | state | action | event | transition),
                schema_name, source_clock, fields[] {name, path, component_names?},
                has_payload
observations[] states[] actions[] events[] transitions[]
                occurrence_id, topic, timestamp_ns, values, payload?, native_ref?
payload         PayloadRef {uri, checksum, size_bytes, media_type,
                selector? {kind video_frame, timestamp_ns}}, the same for every
                source
```

- The window's clock, the frame timeline's clock and every stream's clock are
  declared once in `clock_domains`, and nothing else is declared
  ([Ingress boundaries](./ingress-boundaries.md) §4.1). A recording Episode
  declares every clock with origin `source`; a LeRobot Episode declares
  `lerobot.frame_time` with origin `unit`, so two LeRobot Episodes' timestamps
  are never comparable.
- With a frame timeline, its clock is the window's and has origin `unit`, the
  window is `[0, t(frame_count − 1) + 1)`, every occurrence on that clock lies
  on a frame of the grid, and every `transition` stream has exactly one
  occurrence per frame. `fps` is stored only there, never on the clock domain;
  frame indices are recovered from timestamps and never stored. Without a
  frame timeline no stream is a `transition` stream. Recording Episodes are
  asynchronous and declare none.
- A field with `component_names` holds lists of exactly that length.
- A `PayloadRef` names no source; that its URI names bytes of the Episode's own
  source is a registration check (§5).
- A video frame is selected by its presentation time in the video's own time
  base (a `video_frame` selector), never by a canonical timestamp; the
  reference's checksum and size are the whole file's.
- Every source occurrence in the window is kept; duplicates stay separate
  occurrences; nothing is resampled, interpolated, forward-filled, padded or
  associated.
- Values are bool, integer, finite float, string or a list of numbers, as
  decoded. Non-finite floats are not representable and fail the build.
- Recording observation payloads are SceneOps-owned `OBSERVATION_PAYLOAD`
  artifacts, owned by the RobotRun and shared with Scene builds of the same
  RobotRun (same message + extraction = same artifact id), named by the
  Logical URI of their key `{robot_run_id}/{artifact_id}`. LeRobot images stay
  in the preserved videos.
- The manifest holds no DatasetVersion, episode id, status, outcome, control
  frequency, SourceSnapshot, upstream name or revision, licence or execution
  context. Only facts the source carries appear; labels and outcomes are
  derived or separately imported.

Manifests are stored write-once at
`{canonical_root}/episodes/{episode_id}/manifest-<sha256>.json` (one object per
revision, whichever DatasetVersions pin it) and read only through a pinned
checksum and a strict canonical parse.

## 5. Identity, registration, units and revisions

```text
episode_id     episode_id_for(source): f(source_kind, source_id = robot_run_id |
               content_id, unit_key); no DatasetVersion (Ingress boundaries §6)
revision       (episode_id, manifest_checksum); artifact id
               manifest_artifact_id_for(episode_id, manifest_checksum)
revisions      inserted if absent: identical → no-op · new unit or new
               fingerprint → insert · same fingerprint, other checksum → conflict
result         the source (source_kind, source_id), the producer fingerprint, and
               the complete pin (domain, unit_id, manifest_artifact_id,
               manifest_checksum) of every input revision
```

`REGISTER_EPISODES` takes the manifest artifacts of one build (one source, one
fingerprint) and applies the Scene registrar's gate and rules
([Scene domain](./scene-domain.md) §3): every manifest is read back from the
store and verified, and every payload reference is checked against the
unit's own registered source, all input manifests in one batch
(`sceneops_worker.payload_registration`):

```text
source binding      recording: the RobotRun is registered and the manifests were
                    built from its recording bytes; content: the SourceContent is
                    registered with exactly the bound content_checksum
recording payloads  the URI is a payload key {robot_run_id}/{artifact_id} of the
                    unit's RobotRun, whose OBSERVATION_PAYLOAD ArtifactRecord is
                    owned by it, stored where the URI resolves, with exactly the
                    pinned checksum, size and media type
content payloads    the URI names a path of the unit's own content's inventory,
                    where the inventory places it, with exactly its sha256 and
                    size; the files were hashed when the content was registered
every object        present in the store with the pinned size (one listing per
                    RobotRun or content)
```

Units and revisions are then registered additively in one transaction, in unit
id order. Registration names no DatasetVersion and never writes a member or a
summary.

`EpisodeRecord` (`episodes`) is the unit: `episode_id`, `source_kind` and the
source column of that kind (`robot_run_id` or `content_id`, exactly one set),
`unit_key`. `EpisodeRevisionRecord` (`episode_revisions`) projects one manifest:
`manifest_artifact_id`, `manifest_checksum`, `producer_fingerprint`, the
window, per-role observed topics and counts (transitions included). Both are
immutable; there is no status and no current revision. Readiness derives from
the latest validation run of the revision being assessed. Validation and
profiling take revision pins (`revisions`, one per unit) and read no
DatasetVersion. Alignment is version-scoped: it aligns members of one
DatasetVersion, and an unpinned `ALIGN_EPISODE` input resolves to the revision
that version pins (a pinned one must name a registered revision of the
Episode). Schema: [Data model](./data-model.md) §3.

## 6. API

Read-only; building, validation and profiling run through pipelines or Jobs,
composition through `COMPOSE_DATASET_VERSION`. A revision-level read names its
revision by `manifest_checksum` or through a DatasetVersion that pins it,
exactly one of the two.

```text
GET /api/v1/episodes?robot_run_id=|content_id=     units, optionally of one source
GET /api/v1/episodes/{episode_id}                  the unit and every revision
GET /api/v1/episodes/{episode_id}/manifest?manifest_checksum=
                                                   one revision, checksum-verified; or
GET /api/v1/episodes/{episode_id}/manifest?dataset_id=&dataset_version=
                                                   the revision the version pins
GET /api/v1/episodes/{episode_id}/quality?manifest_checksum=   (or ?dataset_id=&dataset_version=)
                                                   validation / profile of that revision
GET /api/v1/datasets/{id}/versions/{v}/episodes    the revisions the version pins
GET /api/v1/artifacts?owner_type=episode&owner_id={episode_id}
```

## 7. Downstream

Alignment, aligned validation/profiling, learning-data export and curation
consume a pinned canonical revision; see
[Robot learning data layer](./robot-learning-data.md). Alignment carries an
observation payload as a reference value (its Logical URI and its checksum,
size, media type and selector), the same for every source, and reads no
payload. It refuses an Episode with a `transition` stream with an explicit
error (`UnsupportedEpisodeContentError`) instead of dropping or misreading
that content ([Limitations](./limitations.md) §6).
