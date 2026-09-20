# Episode Domain

An `Episode` is a task-oriented observation+action window segmented out of
a robot recording — distinct from a `Scene`, which is a snapshot-style
sensor observation unit. Where Scene answers "what did the sensors see at
this moment," Episode answers "what did the robot observe and do during
this task." `EpisodeRecord` (see [Data model](./data-model.md) §4) is the
canonical unit; there is currently no Episode-domain equivalent of
`DatasetManifest`.

## 1. Pipeline

```text
RAW_LOG_EPISODE_BUILDING pipeline
  build_episodes -> register_episode -> validate_episode -> profile_episode
```

`build_episodes` and `register_episode` are sequential
(`register_episode` depends on `build_episodes`'s output). `validate_episode`
and `profile_episode` both depend only on `register_episode`, not on each
other, and can be thought of as two independent quality stages over the
same registered episode set — mirroring Scene's `validate_scene`/
`profile_scene` split.

`RAW_LOG_EPISODE_BUILDING` is deliberately a separate `PipelineType` from
`RAW_LOG_SCENE_BUILDING`, even though both start from the same raw
rosbag/MCAP recording — they produce different record types
(`EpisodeRecord` vs. `SceneRecord`) with different segmentation semantics
and can run independently of each other over the same underlying recording.

## 2. Input: `EpisodeSource`

`build_episodes` reads a `RosbagAdapter`-decoded recording through
`EpisodeSource` (`packages/sceneops-core/sceneops_core/episodes/schemas/source.py`):

```text
EpisodeSource
  frames: list[RawSensorFrameManifest]   camera/lidar sensor frames -> observation channels
  robot_states: list[RobotStateRecord]   position/velocity/... -> observations,
                                         steering/throttle/brake -> actions
  missions: list[MissionRecord]          mission boundaries -> segmentation signal
```

This is deliberately narrower than what Scene's raw-log pipeline reads:
`EpisodeSource` excludes `RawLogManifest`/`RawLogFrameIndex` (Scene-owned
artifacts — channel/modality summaries, calibrations, ego poses) that
`EpisodeBuilder` never actually used, even when `build_episodes` was
producing them as an unused side effect before this schema existed.

## 3. Segmentation strategies

`EpisodeSegmentationConfig` (`episodes/schemas/config.py`) supports three
strategies:

```text
MISSION_BOUNDARY (default)   one Episode per dated Mission; falls back to
                              WHOLE_RUN when no dated Mission exists
WHOLE_RUN                    the entire recording becomes a single Episode
FIXED_WINDOW                 fixed-duration windows (requires
                              fixed_window_duration_ms > 0)
```

`MISSION_BOUNDARY` is the default because it reproduces `EpisodeBuilder`'s
original behavior from before this config existed as an explicit knob.
Segmentation provenance — which strategy produced a given episode, and its
index within the source recording — is recorded on `EpisodeLineage`
(`segmentation_strategy`, `segment_index`), embedded in the episode
manifest rather than as a DB column.

## 4. EpisodeStatus: registration only, not quality

```text
CREATED -> REGISTERED
```

That's the entire lifecycle. Unlike `SceneStatus`, `EpisodeStatus`
deliberately never encodes validate/profile outcomes — `BUILT`/`VALIDATED`/
`FAILED` were removed from the enum entirely (zero write sites, zero
persisted rows). Episode readiness is derived **purely** from
`EpisodeRecord` plus the latest `EpisodeValidationRunRecord`/
`EpisodeProfileRunRecord` — never from `EpisodeRecord.status` — see
[Quality and run records](./quality-and-runs.md) §3
(`apps/api/app/domains/episodes/quality.py`). This is a deliberate
divergence from Scene's model (where `SceneStatus` *does* fold in
validate/profile outcomes — see [Scene domain](./scene-domain.md) §4), not
an oversight to reconcile later.

## 5. Artifact ownership and API reachability

Episode follows the same "producer owns the `ArtifactRecord`" invariant as
Scene (see [Jobs and pipelines](./jobs-and-pipelines.md) §7): `build_episodes`
owns the `EPISODE_MANIFEST` artifact it writes; `register_episode` only
reads it.

Unlike Scene, `ArtifactModel` has no dedicated `episode_id` column — Episode
artifacts are only reachable through the generic `owner_type=episode` /
`owner_id={episode_id}` query, and `EpisodeRecord.episode_manifest_uri`
already answers "where's the manifest" directly on the detail response, so
a dedicated `/{episode_id}/artifacts` convenience route was judged
unnecessary rather than a gap to fill.

Representative routes (all read-only — `validate_episode`/`profile_episode`
are only triggered through Jobs/Pipelines, never through this API):

```text
GET /api/v1/episodes
GET /api/v1/episodes/{episode_id}
GET /api/v1/episodes/{episode_id}/quality
GET /api/v1/artifacts?owner_type=episode&owner_id={episode_id}
```

`list_episodes` supports filtering by `dataset_id`, `dataset_version`,
`robot_run_id`, and `mission_id` — the last two are the practical way to
find "the episodes that came from this robot recording," since Episode has
no `DatasetManifest`-equivalent index.
