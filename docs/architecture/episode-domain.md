# Episode Domain

An `Episode` is a canonical, source-faithful, task/behavior-oriented
projection of one registered RobotRun recording. It keeps the recorded
observation, state, action and task/event streams of a window, each at its
own timestamps on its own declared clock. Where a Scene answers "what did
the sensors see in this window," an Episode answers "what did the robot
observe, report and do during this task."

Scene and Episode are siblings over the same RobotRun: each is built
directly from the recording, neither reads the other, and either may be
built first. The authoritative contract is ADR-007 §31 (Amendment A6).

**Canonical Episode preserves asynchronous source streams. Temporal
alignment is a derived (L3) operation** (`ALIGN_EPISODE` → `AlignedEpisode`,
the first stage of the `EPISODE_LEARNING_DATA_BUILDING` pipeline; see
[Robot learning data layer](./robot-learning-data.md)).

## 1. Pipeline

```text
RECORDING_EPISODE_BUILDING  (one robot_run_id per run)
  build_recording_episodes -> register_episodes -> validate_episode
                                                -> profile_episode (optional)
```

```text
build_recording_episodes   resolve_recording(robot_run_id) -> L1 conformance check
                           -> plan_recording_episodes (pure) -> OBSERVATION_PAYLOAD
                           artifacts -> EPISODE_MANIFEST artifacts (write-once)
register_episodes          the only writer of Episode units and revisions, and
                           of the DatasetVersion's Episode members and
                           episode_count
validate_episode /         run records pinned to the revision they assessed
profile_episode
```

The job takes `dataset_id`, `dataset_version`, `robot_run_id` and
`build_config` only; recording URIs, paths and robot ids are rejected. A
DatasetVersion spanning several RobotRuns takes one pipeline run per
RobotRun.

## 2. Build configuration

`RecordingEpisodeBuildConfig`
(`packages/sceneops-core/sceneops_core/episodes/recording_build.py`) holds
every semantic decision; its normalized form is the producer's
`ProducerInfo.build_config` and part of the fingerprint.

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

## 3. Segmentation and windows

An Episode window is a half-open interval `[start, end)` in one
segmentation clock (`RecordingSegmentSource.source_clock`): the clock the
segmentation declares, or for `event_markers` the event source's clock. Every
stream must be placeable on it (its time is in that clock, or the clock is
`mcap_log_time` / `mcap_publish_time`). Streams keep their own clocks;
timestamps in another clock are never compared with the window or converted.

```text
whole_recording  [earliest, latest + 1)                          segment recording
fixed_duration   [k·d, (k+1)·d): grid at the clock's zero, non-empty   segment <k>
event_markers    [start marker, end marker + 1) per task key      segment task-<key>-<n>
```

The unit key carries the segmentation (ADR-015 §6.3,
`sceneops_core.provenance.segmentation`):
`unit_key = <policy>-<sha256(boundary document)[:16]>/<segment>`. The boundary
document is `RecordingEpisodeBuildConfig.segmentation_boundary()`: the policy,
clock and duration, or for `event_markers` the marker topic, its time policy
and decoding, the field *paths* behind `key_field` / `state_field`, and the
start / end values. Another segmentation is other units; another stream or
field selection under the same segmentation is a new revision of the same
units.

Unpaired markers (an end without a start, a second start, a start never
ended) and a build that yields no Episode fail the job. Windows are never
derived from `RobotRun.started_at` / `ended_at`.

## 4. EpisodeManifest

`sceneops.episode_manifest/v1`
(`packages/sceneops-core/sceneops_core/episodes/schemas/manifests.py`):

```text
lineage        RecordingSegmentSource (robot_run_id, recording artifact + checksum,
               window, unit_key) + ProducerInfo (fingerprint re-derivable)
streams[]      topic, role, recorded schema_name, source_clock, fields, has_payload
observations[] states[] actions[] events[]
               occurrence_id (<topic slug>-<rank>), topic, timestamp_ns, values, payload?
```

- Every recorded occurrence in the window is kept; duplicates stay separate
  occurrences; nothing is resampled, interpolated, forward-filled, padded or
  associated.
- Values are bool, integer, finite float, string or a list of numbers, as
  decoded. Non-finite floats are not representable in v1 and fail the build.
- Observation payloads are SceneOps-owned `OBSERVATION_PAYLOAD` artifacts,
  owned by the RobotRun and shared with Scene builds of the same RobotRun
  (same message + extraction = same artifact id).
- The manifest holds no DatasetVersion, episode id, status, task, outcome,
  control frequency or execution context. Only facts the recording carries
  appear; labels and outcomes are derived or separately imported.

Manifests are stored write-once at
`{canonical_root}/episodes/{episode_id}/manifest-<sha256>.json` (one object per
revision, whichever DatasetVersions pin it) and read only through a pinned
checksum and a strict canonical parse.

## 5. Identity, registration, units and revisions

```text
episode_id     episode_id_for(source): f(source_kind, source_id = robot_run_id,
               unit_key); no DatasetVersion (Ingress boundaries §6)
revision       (episode_id, manifest_checksum); artifact id
               manifest_artifact_id_for(episode_id, manifest_checksum)
revisions      inserted if absent: identical → no-op · new fingerprint → new
               revision · same fingerprint, other checksum → conflict
members        scope (DatasetVersion, robot_run_id): one producer fingerprint
                 same fingerprint            -> unchanged
                 different, replace=false    -> conflict, no change
                 different, replace=true     -> unpin / repin / pin the complete set
```

`REGISTER_EPISODES` verifies every manifest and payload reference, checks
that the manifests were built from the RobotRun's recording bytes, registers
units and revisions additively, then locks the DatasetVersion row, applies the
scope rule to its members and recomputes `episode_count` in one transaction.

`EpisodeRecord` (`episodes`) is the unit: `episode_id`, `source_kind`,
`robot_run_id`, `unit_key`. `EpisodeRevisionRecord` (`episode_revisions`)
projects one manifest: `manifest_artifact_id`, `manifest_checksum`,
`producer_fingerprint`, the window, per-role observed topics and counts. Both
are immutable; there is no status and no current revision. Readiness derives
from the latest validation run of the revision being assessed, and
validation, profiling and alignment read the revision a DatasetVersion member
pins (an unpinned `ALIGN_EPISODE` input needs `dataset_id` /
`dataset_version`; a pinned one must name a registered revision of the
Episode). Schema: [Data model](./data-model.md) §3.

## 6. API

Read-only; building, validation and profiling run through pipelines.

```text
GET /api/v1/episodes?robot_run_id=                 units
GET /api/v1/episodes/{episode_id}                  the unit and every revision
GET /api/v1/episodes/{episode_id}/manifest?dataset_id=&dataset_version=
                                                   the revision the version pins,
                                                   checksum-verified
GET /api/v1/episodes/{episode_id}/quality?dataset_id=&dataset_version=
                                                   validation / profile of that revision
GET /api/v1/artifacts?owner_type=episode&owner_id={episode_id}
```

## 7. Downstream

Alignment, aligned validation/profiling, learning-data export and curation
consume a pinned canonical revision; see
[Robot learning data layer](./robot-learning-data.md).
