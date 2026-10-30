# Native dataset integration architecture (M3.0)

> Point-in-time architecture finalization for M3 of the
> [multi-source implementation plan](./multi-source-implementation-plan.md). It recommends
> resolutions of the [ADR-015](../adr/015-multi-source-platform.md) open questions that
> gate M3, refines the boundaries M3 implements, and records the evidence read from real
> data. The recommendations were decided on 2026-10-22. §9 records each decision and where
> it differs from the recommendation, and ADR-015 §16 records them as amendments.
> Recommendation text in §3 and §4 is kept as written. §5 shows the decided schemas. It is not a
> current contract, and nothing here is implemented.

```text
date      2026-10-22
branch    refactor/multi-source-platform
HEAD      4b11e5e (clean)
nuScenes  data/raw/nuscenes, v1.0-mini (9.4 GB), read with python3 -I (json only)
LeRobot   fetched read-only into the session scratchpad (not committed):
            lerobot/pusht              rev 7628202a2180972f291ba1bc6723834921e72c19, all
                                       7 dataset files (7,684,382 bytes)
            lerobot/aloha_static_coffee rev b144896feb1f37398a862927b22cd3abdf005a6b,
                                       README, meta/*, data/chunk-000/file-000.parquet
          read with the workspace pyarrow 25.0.1 (.venv, python -I); no lerobot SDK
platform  sceneops_core models at HEAD (EpisodeManifest v1 via sceneops_core.episodes.testing)
docker    docker system df, df inside sceneops-postgres-1 / sceneops-minio-1 (read-only)
```

Labels: **VERIFIED** = measured on the data or code above; **Ready** = recommended for
acceptance as written; **Review** = a recommendation that changes an accepted text or scope
and needs an explicit decision; **Decided** = the 2026-10-22 decision (§9).

## 1. Summary

| Item | Recommendation | Recommended status | Decided (§9) | M3 impact |
| --- | --- | --- | --- | --- |
| OQ-1 snapshot storage | always copy into SceneOps storage; no in-place reference in M3–M5 | Ready | as recommended | publication copies; disk prerequisite (§7) |
| OQ-2 LeRobot time | `t_ns = round_half_even(frame_index · 10⁹ / fps)` on `lerobot.frame_time`, origin `unit`; the builder rejects rows whose float32 `timestamp` is not `float32(frame_index / fps)` | Ready | as recommended | Episode builder normalization and format check |
| OQ-2a where fps lives | the clock domain declares `frame_rate`; per-occurrence float32 `timestamp` / `frame_index` fields are not kept | **Review** | **amended**: `fps` on the Episode `frame_timeline`, not on the clock domain | EpisodeManifest v2 `frame_timeline` |
| OQ-3 inline values | inline numeric values; no native column reference type | Ready | as recommended | EpisodeManifest v2 keeps inline values |
| OQ-5 nuScenes unit key | `scene.token`; LeRobot `str(episode_index)` | Ready | as recommended | unit keys, reference contract |
| OQ-6 native nuScenes labels | in M3 as the last, separable slice (S6), together with the label anchor change | **Review** | S6, **sample-anchored** with `group_ref = sample.token`, independent of sample views | S6 |
| OQ-9 recording clock origin | origin `source` for every recording clock, set by the builder, not configurable in M3 | Ready | as recommended | recording builders emit clock domains; semantics bump |
| OQ-10 descriptor correction | keep "conflict, fails"; no correction mechanism; reference descriptors carry `license` only | Ready | as recommended | none beyond S1 tests |
| D1–D14 (§4) | contract details found while validating against real formats | Ready, except D3, D8, D9: **Review** | D3, D8 as recommended; **D9 amended**: `transition` streams, not events | S1–S4 |

## 2. Evidence

### 2.1 LeRobot v3.0 (VERIFIED)

```text
                          pusht                          aloha_static_coffee
fps, episodes, frames     10, 206, 25,650                50, 50, 55,000
episode length            49 – 246                       1,100 (all)
data files / video files  1 / 1 (one camera)             1 / 1 per camera (four cameras)
vector column type        fixed_size_list<float>[2]      list<float> (14)
names form                dict {"motors": [...]}         dict {"motors": [...]}
frame_index               dense from 0 in every episode  dense from 0 in every episode
index                     contiguous global row number   contiguous global row number
timestamp == float32(frame_index / fps)
                          25,650 / 25,650 rows           55,000 / 55,000 rows
round(float64(timestamp) · 10⁹) ≠ round_half_even(frame_index · 10⁹ / fps)
                          20,440 rows, max 763 ns        52,750 rows, max 916 ns
videos/<key>/from_timestamp  float64 = Σ previous lengths / fps (max deviation 0.001 ns)
tasks.parquet             the task string is the pandas index column `__index_level_0__`
next.success              false on every frame           (absent)
```

- `timestamp` is a float32 function of `frame_index`, not an independent measurement.
  `frame_index` and `fps` are the source facts. Using `timestamp · 10⁹` would move 80–96 %
  of all frames off the exact grid by up to 0.9 µs.
- Episode boundaries are row ranges in a shared Parquet file and time ranges in a shared MP4,
  so a frame is addressable only as (file, time in that file).
- **Pinning.** For the five LFS files of pusht, the Hugging Face LFS oid equals the file's
  sha256 (5 / 5). The non-LFS files (`meta/info.json`, `meta/stats.json`, `README.md`) are
  identified upstream only by a git blob sha1.

### 2.2 Manifest size for inline values (OQ-3, VERIFIED)

Built with the current `EpisodeManifest` model (`sceneops_core.episodes.testing`), real
values, `ArtifactPayloadRef`-sized frame references:

```text
aloha episode 0   1,100 frames, 3 numeric streams × 14 floats + 4 camera streams
                  7,700 occurrences   2.78 MB   build 0.10 s  serialize 0.05 s  strict load 0.16 s
                  numeric only        1.31 MB                                   strict load 0.07 s
pusht episode 0   161 frames          0.10 MB                                   strict load 0.01 s
estimate with SourceFileRef-sized frame refs: aloha 3.46 MB / episode (3,143 B / frame),
                                               pusht 916 B / frame
float32 → float64 widening → shortest JSON → float32: bit-exact on 15,400 aloha values
```

### 2.3 nuScenes v1.0-mini (VERIFIED)

- `scene.token` and `scene.name` are both unique; tables join on tokens.
- `(channel, sample_data.timestamp)` is unique across all 31,206 readings, so an anchor of
  (content, channel, clock, timestamp) is unambiguous.
- One `calibrated_sensor` per channel within every scene.
- `sample.timestamp` equals its `LIDAR_TOP` keyframe timestamp for 404 / 404 samples, so
  keyframe group timestamps lie inside the reading window.
- `sample_data` carries `width` / `height`; no payload byte needs to be read to build a Scene.
- `fileformat` is `pcd` for both lidar (`.pcd.bin`) and radar (`.pcd`), so a media type must
  come from the channel modality and file suffix, not from `fileformat`.
- `scene-0061`: window `[1532402927604844000, 1532402946826796001)` ns (first reading
  2018-07-24 03:28:47.604844 UTC; the log is named `…2018-07-24-11-22-45+0800`, i.e. 03:22:45
  UTC, consistent with an epoch clock), 43.107 ms before the first sample and 29.279 ms after
  the last, 39 samples, 4,699 annotations.
- **Footprint.** The 9.4 GB raw tree is 5.34 GB of sensor files (10 scenes), **4.63 GB of
  `can_bus/`** (7,832 files of the CAN expansion for every nuScenes scene; only 80 files,
  47.7 MB, belong to mini scenes), 33.3 MB of tables (13 files), 5.8 MB of maps (4 files).
  Every mini scene is the only scene of its log except 1077 / 1094 / 1100, so a per-log
  pattern isolates `scene-0061`:

  ```text
  selection                                   files    bytes
  scene-0061 slice (tables, maps, licence,    2,989    519,531,462   (0.48 GiB)
    its log's samples/sweeps, its CAN)
  full mini, CAN of the 10 mini scenes only   31,304   5,422,639,642 (5.05 GiB)
  pusht (README.md, data/**, meta/**,         7        7,684,382
    videos/**)
  ```

### 2.4 Platform facts at HEAD (VERIFIED)

- `sceneops_core.sources` (inventory, declaration, descriptor, ids), `ContentUnitSource`,
  `ContentSourceRevision`, `PinnedContent` and unit id v2 exist; Scene / Episode lineage
  admits `RecordingSegmentSource` only (`test_ingress_boundaries.py` enforces it).
- `scenes` / `episodes`: `robot_run_id NOT NULL`, `CHECK source_kind = 'recording'`,
  `UNIQUE (robot_run_id, unit_key)`. `SceneRecord` / `EpisodeRecord` carry `robot_run_id`.
- Registrars require exactly one RobotRun per registration and verify the recording.
- `RevisionSourceClause` (composition selector) is keyed by `robot_run_id`.
- Recording-only consumers: `sample_views/build.py` compares `anchor.robot_run_id`;
  `episodes/alignment/engine.py` and `inference/detection/samples.py` read
  `payload.artifact_id`.
- `ArtifactStore` has `write_bytes` only: every object is written from memory.
- Baseline build configs use the clocks `sensor.header_stamp` and `vehicle.source_time`.
- The reference verifier already reads raw nuScenes independently
  (`tools/reference/verification/nuscenes_source.py`).

## 3. Open questions

### OQ-1 Snapshot storage — Ready

| Option | For | Against |
| --- | --- | --- |
| **A. Always copy** into `{content_root}/{content_id}/files/{path}` | reproducibility under SceneOps control; offline CI / clean room; one resolver | storage ×1 (5.05 GiB full mini with mini CAN; 7.7 MB pusht) |
| B. In-place pinned reference for content-addressed stores | no copy | HF pins LFS files by sha256 but small metadata files only by git sha1, so SceneOps hashes them anyway; nuScenes has no content-addressed store; network in every build; a second resolver path |

Recommendation: **A** for M3–M5. B stays deferred (ADR §14) and gets no code or field in M3.
The declaration's selection is the place to keep storage proportional: the reference
nuScenes declaration selects the CAN extracts of its own scenes, not the 4.63 GB expansion.

### OQ-2 LeRobot time — Ready; OQ-2a — Review

| Option | Result |
| --- | --- |
| **A. `t_ns = round_half_even(frame_index · 10⁹ / fps)`** | integer ns, deterministic, strictly increasing, invertible given fps (frame spacing ≥ 10⁶ ns ≫ 0.5 ns rounding); the same rule as M4 ticks, so native-rate alignment is exact |
| B. rational clock `(frame_index, fps)` | exact, but breaks the integer-ns contract (`SourceTimestampNs`) for every consumer |
| C. `round(timestamp · 10⁹)` | rejected: off-grid on 80–96 % of frames by up to 916 ns (§2.1) |

Recommendation: **A**, clock `lerobot.frame_time`, origin `unit`, window
`[0, t(length − 1) + 1)`. Format support (builder, loud failure): `fps` is a positive integer,
`frame_index` is dense from 0 in each episode, and `timestamp == float32(frame_index / fps)` on
every row. A dataset with recorded, non-grid timestamps is rejected instead of being placed on
a grid it does not have.

**OQ-2a (Review).** ADR §6.2 says the recorded float32 `timestamp` and `frame_index` "are kept
as stream fields". §2.1 shows `timestamp` is a function of `frame_index`, and `frame_index`
is recoverable exactly from `t_ns` given fps. Recommendation: `ClockDomain` gains an optional
`frame_rate {numerator, denominator}`, declared for a frame-grid clock; occurrences carry no
`timestamp` / `frame_index` value; `native_ref` is the global `index`. Alternative (ADR as
written): keep `frame_index` as a value of every occurrence, about +25 B per occurrence (~7 %
at aloha scale), no schema addition.

*Decided with an amendment (§9): `fps` is on the Episode `frame_timeline`, not on
`ClockDomain`.*

### OQ-3 Inline numeric values — Ready

Recommendation: inline values (as recording Episodes already do); no native column reference
type in M3–M5. The heaviest probed layout is 2.8–3.5 MB and 0.16 s strict load per episode
(§2.2). No size guard or configuration is added; the measured envelope is recorded as a
limitation, and native column references are revisited only when a measured episode manifest
exceeds ~64 MiB or ~2 s strict load (about 20× aloha).

### OQ-5 nuScenes unit key — Ready

Recommendation: `unit_key = scene.token` (the key every table joins on). `scene.name` is
display data: it enters no identity and no manifest. The reference contract and verifier map
`scene-0061` → `cc8c0bf57f984915a77078b10eb33198` from the raw tables. LeRobot
`unit_key = str(episode_index)` (decimal, no padding).

### OQ-6 Native nuScenes labels — Review (scope)

| Option | For | Against |
| --- | --- | --- |
| **A. M3, last slice (S6)** | the anchor change lands with its producer (no dormant variant); M5's `e2e-scene-ml` on native Scenes has labels; anchor proven unambiguous (§2.3) | M3 larger |
| B. M5 | M3 smaller | M5 then carries a contract change (anchor, sample-view index) beside the clean room |

Recommendation: **A**, separable from Scene / Episode acceptance. `IMPORT_LABELS` gains a
snapshot input: it resolves `snapshot_id` → `content_id` once (recorded on the Job), the
nuScenes label reader emits a `LabelSetManifest` anchored on
`(content, content_id, LIDAR_TOP, nuscenes.timestamp, keyframe timestamp)`, and the existing
label storage and pinning are reused. `render-labels` stays the label path for RobotRun Scenes.

*Decided (§9): A. The anchor also carries `group_ref = sample.token` and is resolved
without a sample view.*

### OQ-9 Recording clock origin — Ready

Recommendation: every recording clock (`mcap_log_time`, `mcap_publish_time` and every
header-stamp clock) is declared with origin `source`, fixed by the recording builders, with no
configuration field in M3. Capture's log time is a host clock and a header stamp's epoch is a
property of the robot, which the platform cannot verify from the recording. `unix_epoch` is
used only for content clocks whose format defines an epoch and whose data agrees
(`nuscenes.timestamp`, §2.3). A recording `unix_epoch` assertion is added only when a consumer
needs cross-RobotRun comparison, as an explicit build-config field. Effect: recording
manifests gain `clock_domains[]` (semantics bump); build configs do not change.

### OQ-10 Descriptor correction — Ready

Recommendation: keep ADR §3 ("same declaration, different descriptor → conflict, fails"). No
correction mechanism in M3. Reference descriptors carry one annotation, `license`
(`mit` for pusht, the nuScenes terms identifier for nuScenes), to keep the conflict surface
small. If a correction is ever needed, it is an append-only, separately identified annotation
record per snapshot, never a descriptor rewrite.

## 4. Additional contract details (D1–D14)

| # | Detail | Status |
| --- | --- | --- |
| D1 | **Selection dialect.** Patterns match the whole relative path. `*` and `?` match within one segment; `**` is a whole segment matching zero or more segments; every other metacharacter (`[ ] { } !`) is rejected. Every pattern must match at least one file, so a typo fails publication instead of silently preserving less. Identity-relevant: fixed by `snapshot_id_schema` v1. | Ready |
| D2 | **Build unit selection.** `SNAPSHOT_*_BUILDING` takes an optional `unit_keys`; it is execution scope, not `build_config`, so it never enters the fingerprint. A manifest depends only on its own unit, so building a subset yields the same revisions. Default: every unit the reader enumerates; a unit whose referenced files are not in the inventory fails loudly (never skipped). Needed because the `scene-0061` slice keeps the shared tables, which list ten scenes. | Ready |
| D3 | **SourceFileRef media type** is asserted by the builder (the inventory has none). Registration verifies `path`, `size_bytes`, `sha256` against the inventory and that the content is registered. ADR §6.5 "verifies … media type" is read as "the builder's media type is part of the manifest". | **Review** (ADR wording) → *decided as recommended (§9)* |
| D4 | **Content file location** is derived from the inventory ArtifactRecord: `dirname(inventory.uri)/files/{path}`. No root setting in the worker; moving storage changes ArtifactRecords only. | Ready |
| D5 | **Gate presence check** (R7): Scene / Episode registration also lists the referenced content files (one listing per content prefix) and requires each to be present with its inventory size, as for payload objects. | Ready |
| D6 | **Reader / builder split** (§5.2): readers parse native structures and enumerate units and their files; builders own every canonical decision. | Ready |
| D7 | **Builder modules are format-named** (`sceneops_scenes.nuscenes_builder`, `sceneops_episodes.lerobot_builder`) and return the existing `SceneBuildPlan` / `EpisodeBuildPlan`, so manifest publication is shared with the recording builders. They write no payload objects. | Ready |
| D8 | **Vector component names.** `EpisodeField` gains optional `component_names`, the source's `names` flattened in order (dict `{"motors": [...]}` with one key, or a list); a multi-key dict fails. Without it a 14-vector loses which joint is which before M4 export. | **Review** (schema) → *decided as recommended (§9)* |
| D9 | **`next.*` signals** are selectable as `event` streams with per-frame values kept verbatim; no `EpisodeOutcome` is derived. The reference pusht config selects them. | **Review** (role meaning) → *amended (§9): `transition` streams* |
| D10 | **Composition selector** `RevisionSourceClause` becomes `{domain, source_kind, source_id, producer_fingerprint}`, landing with the unit-table change. | Ready |
| D11 | **Registration input** `REGISTER_SOURCE_SNAPSHOT {descriptor_uri}` (as `REGISTER_ROBOT_RUN {manifest_uri}`); the URI must end in `sources/snapshots/{snapshot_id}/descriptor.json` for its own declaration, and the inventory URI is derived from the same root. | Ready |
| D12 | **Whole-object writes.** Publication uses `write_once` per file from memory. Accepted for M3 (largest probed file 6.9 MB; LeRobot v3 targets 100 MB data / 500 MB video files, `info.json` `*_files_size_in_mb`). Streaming upload is added only if a measured file exceeds the publisher's memory budget. | Ready (limitation) |
| D13 | **LeRobot task events.** One `task` event occurrence at every frame whose `task_index` differs from the previous frame (always frame 0), values `{task_index, task}`; the task string comes from `tasks.parquet` (§2.1). Lossless because frames are dense. | Ready |
| D14 | **Unsupported consumers refuse.** Until M4, Episode consumers that cannot resolve a `SourceFileRef` (alignment, learning export, LeRobot export) refuse a content-sourced Episode with an explicit error instead of failing on a missing `artifact_id`. | Ready (limitation) |

Video frame selector: `frame.timestamp_ns = round_half_even((from_timestamp + frame_index /
fps) · 10⁹)`, with `from_timestamp` taken as its exact float64 value and one rounding of the
exact sum, in the time base of that MP4 file. M3 stores it and decodes nothing; frame-exact
decoding is verified by the first M4 consumer.

## 5. Boundaries and schemas

### 5.1 Process flow

```text
local source tree + SnapshotDeclaration + annotations
apps/publisher publish-snapshot (DB-free)       sceneops_sources.publication
  select (D1) → hash → SourceContentInventory → content_id; declaration → snapshot_id
  files   {root}/sources/contents/{content_id}/files/{path}   write_once each (reuse = dedup)
  inventory {root}/sources/contents/{content_id}/inventory.json  write_once
  descriptor {root}/sources/snapshots/{snapshot_id}/descriptor.json  written last = published
REGISTER_SOURCE_SNAPSHOT {descriptor_uri}      worker; ADR §5 rules; one transaction
  → source_contents (+ inventory ArtifactRecord), source_snapshots (+ descriptor ArtifactRecord)
SNAPSHOT_SCENE_BUILDING / SNAPSHOT_EPISODE_BUILDING {snapshot_id, build_config, unit_keys?}
  build    snapshot_id → content_id once (Job result) → PinnedContent + ContentFiles
           → reader → builder plan → manifests (write_once, unit-scoped keys)
  register REGISTER_SCENES / REGISTER_EPISODES (manifest_artifact_ids), gate incl. D3/D5
  compose  optional COMPOSE_DATASET_VERSION (pins), unchanged
```

### 5.2 Responsibilities

| Boundary | Owns | Never | Interface |
| --- | --- | --- | --- |
| **SourceContent / SourceSnapshot** | bytes (content) and one upstream declaration (snapshot) | interpret, decide units / clocks, update a record | `sceneops_core.sources` records; ids by ADR §3 |
| **Source Registry** (PostgreSQL) | identity rows, content ↔ snapshot binding, inventory / descriptor ArtifactRecords, conflict rules | store file bytes, one ArtifactRecord per file, a status | `source_contents`, `source_snapshots`, repositories |
| **Native artifact storage** (object store) | preserved files, inventory, descriptor, write-once | rename, transcode, filter inside a file, delete | key layout §5.1; location reached only via the inventory ArtifactRecord (D4) |
| **Native readers** (`sceneops_sources.{nuscenes,lerobot}`) | parse native tables / Parquet / JSON into typed native records; derive the format version from the bytes; enumerate units and each unit's referenced paths; referential integrity | ns normalization, clock domains, modality or role mapping, manifests, unit ids, DB, network | `read_*_content(files: ContentFiles)` |
| **PinnedContent / ContentUnitSource** | the pinned content revision and per-unit binding a builder emits | name a snapshot, descriptor field, location | `sceneops_core.provenance` (exists) |
| **Canonical builders** | unit boundary, windows, clock domains, normalization, channel / stream semantics, media types, payload refs, manifest | read a descriptor, write payload objects, touch DB | `plan_*(pinned, content, config, unit_keys) → *BuildPlan` |
| **Canonical registration** | gate (manifest read-back, `ArtifactPayloadRef` objects, `SourceFileRef` inventory match and object presence), unit + revision rows | write bytes, read a snapshot, touch a DatasetVersion | `REGISTER_SCENES` / `REGISTER_EPISODES`, scope = one source + one fingerprint |

Native readers and builders are different modules with one edge (builder → reader), so the
probe's two formats share no abstraction: there is no generic reader protocol beyond
`ContentFiles`.

### 5.3 Schemas

Decided schemas (§9); recommendation-time differences are noted there.

```text
Core (sceneops_core)
  SnapshotSelection        + D1 dialect validation
  ClockDomain              clock, origin unix_epoch | source | unit, resolution_ns
                           (no sampling rate: OQ-2a)
  PayloadRef union         ArtifactPayloadRef {kind:"artifact", artifact_id, checksum,
                             size_bytes, media_type}                       (current shape + kind)
                           SourceFileRef {kind:"source_file", content_id, path, sha256,
                             size_bytes, media_type, frame? {timestamp_ns}}
                             media_type assigned by the format builder (D3)
  SceneManifest v3 / EpisodeManifest v2   (one schema for both source kinds)
                           lineage.source: SourceBinding; clock_domains[] (sorted by clock,
                           every used clock declared once); payload union; native_ref? on
                           observations, groups, occurrences
  EpisodeManifest v2 only  frame_timeline? {clock, fps (positive int), frame_count}  (OQ-2a)
                             every occurrence on `clock` is round_half_even(k·10⁹/fps) for
                             one k in [0, frame_count); window [0, t(frame_count−1)+1)
                           EpisodeField.component_names? (D8): list length = value length
                           EpisodeStreamRole + transition, occurrence list `transitions`
                             (D9): one occurrence per frame, values in source type
  LabelSetManifest v2      ObservationAnchor {source_kind, source_id, channel,
                             source_clock, timestamp_ns, unit_key? (unit origin only),
                             group_ref? (native_ref of the annotated group)}       (OQ-6)
  SourceContentRecord      content_id, format, content_checksum, inventory_artifact_id,
                           file_count, total_bytes, registered_at
  SourceSnapshotRecord     snapshot_id, content_id, format, upstream, upstream_revision,
                           selection_checksum, descriptor_artifact_id, descriptor_checksum,
                           registered_at
  RevisionSourceClause     domain, source_kind, source_id, producer_fingerprint      (D10)
  ArtifactKind             + SOURCE_CONTENT_INVENTORY, SOURCE_SNAPSHOT_DESCRIPTOR
  ArtifactOwnerType        + SOURCE_CONTENT, SOURCE_SNAPSHOT

DB (sceneops_db)
  source_contents          PK content_id; FK inventory_artifact_id RESTRICT; UPDATE refused
  source_snapshots         PK snapshot_id; FK content_id RESTRICT; FK descriptor_artifact_id
                           RESTRICT; UPDATE refused
  scenes / episodes        + content_id FK source_contents RESTRICT; robot_run_id nullable;
                           UNIQUE (robot_run_id, unit_key), UNIQUE (content_id, unit_key);
                           CHECK exactly one of robot_run_id / content_id, matching source_kind
                           (the per-kind column design measured in Step 4: a single nullable
                           source column loses uniqueness, NULLS NOT DISTINCT false-conflicts)

Jobs
  REGISTER_SOURCE_SNAPSHOT params {descriptor_uri}
                           result {snapshot_id, content_id, content: created | existing,
                                   snapshot: created | existing, file_count, total_bytes}
  BUILD_SNAPSHOT_SCENES / BUILD_SNAPSHOT_EPISODES
                           params {snapshot_id, build_config, unit_keys?}
                           result {snapshot_id, content_id, manifest_artifact_ids, unit count}
  REGISTER_SCENES / REGISTER_EPISODES
                           params unchanged; result: robot_run_id → {source_kind, source_id}
  IMPORT_LABELS            params: exactly one of document_uri (current) or snapshot_id
                           (native import, S6); result + {snapshot_id, content_id}
```

### 5.4 Interfaces

```text
sceneops_sources (new package; edges: sceneops_core, sceneops_storage)
  select_files(source_dir, selection) -> list[path]
  build_inventory(source_dir, paths) -> SourceContentInventory     streaming sha256
  publish_snapshot(store, root_uri, source_dir, declaration, annotations) -> PublishedSnapshot
  ContentFiles (Protocol)  inventory; local_path(path) -> Path     verified sha256 + size
    StoredContentFiles     materializes from the object store into a job workdir
    LocalContentFiles      a local directory checked against an inventory (tests, tools)
  nuscenes.read_content(files) -> NuScenesContent    format_version, units (token, name),
                                                     per-unit sample_data, samples, ego poses,
                                                     calibrations, referenced paths
  lerobot.read_content(files) -> LeRobotContent      codebase_version, fps, features, tasks,
                                                     per-episode Arrow rows + video segments

  nuscenes.read_annotations(files) -> samples (token, timestamp, scene) and their
                                      sample_annotations                      (S6)

sceneops_scenes.nuscenes_builder   plan_nuscenes_scenes(...) -> SceneBuildPlan
sceneops_episodes.lerobot_builder  plan_lerobot_episodes(...) -> EpisodeBuildPlan
sceneops_derived.nuscenes_labels   nuScenes annotations -> LabelSetManifest (S6; new edge
                                   sceneops_derived → sceneops_sources)

sceneops_db   SourceContentRepository / SourceSnapshotRepository (insert-if-absent and
              re-decide, the _revisions.py pattern); unit repositories by source kind

apps/worker   handlers REGISTER_SOURCE_SNAPSHOT, BUILD_SNAPSHOT_SCENES, BUILD_SNAPSHOT_EPISODES;
              pipelines SNAPSHOT_SCENE_BUILDING, SNAPSHOT_EPISODE_BUILDING;
              registration gate verify_source_file_refs; one payload locator dispatching on
              PayloadRef.kind (the only source branch downstream, MS-10)
apps/publisher publish-snapshot (raw-source mount, as acquisition services)
apps/api      GET /source-snapshots, /source-snapshots/{id}, /source-contents/{id};
              Scene / Episode list filter by source_kind, source_id
```

## 6. Verification approach (R5)

Each slice extends `tools/reference/verification` from the documented rules before the slice's
first platform run: nuScenes from the existing raw reader (`nuscenes_source.py`), LeRobot
from a new pyarrow raw reader in tools. Negative controls per slice: a dropped observation /
occurrence, a 1 ns shift, a changed file byte, a wrong pin, and a `unit`-origin timestamp given
an absolute anchor. The verifier recomputes `content_id`, `snapshot_id`, unit ids,
`t_ns`, windows and `SourceFileRef` checksums from raw bytes.

## 7. Docker storage (VERIFIED 2026-10-22)

```text
Docker VM filesystem   58.4 GiB, 4.3–4.4 GiB free (92–93 % used)
  images               15.87 GB (13.45 GB reclaimable; lerobot-integration 6.99 GB,
                       dataset-replay 2.62 GB not needed in M3)
  build cache          39.64 GB (19.61 GB reclaimable)
  volumes              67 MB; reference MinIO /data 920 KB (reset)
host disk              41 GiB free
```

| Workload | Content | With preflight margin (+25 %, +1 GiB WAL, 2 GiB floor) |
| --- | --- | --- |
| S1 pusht + scene-0061 slice preservation | 0.49 GiB | ~3.6 GiB |
| S3 pusht Episodes | < 0.1 GiB | fits |
| S4 scene-0061 Scenes + RobotRun scene-0061 (recording 356 MB + payload objects) for cross-source check | ~1.4 GiB | ~4.8 GiB: **does not fit today** |
| full mini snapshot (M5) | 5.05 GiB | ~9.3 GiB |
| worker / publisher image rebuild | ~2 GB transient | must precede workloads |

Safe prerequisites (none is a platform change):

1. Free Docker VM space **by the user** (for example the 19.6 GB of reclaimable build cache)
   before S1's image rebuild. Implementation work never prunes or deletes Docker resources.
2. Pin the LeRobot fixture locally (`data/raw/lerobot/pusht` at `7628202a`, 7.7 MB) with a
   checksum lock, through the reference-data tooling, so tests never read the HF hub.
3. Extend `capacity-preflight` with snapshot workloads (content bytes from the inventory)
   in S1, before the first publication.
4. Run integration on disposable runtimes (`disposable_env.py`); rebuild only the images a
   slice needs, with `docker compose … build <service>` (not `make compose-build`, which
   re-locks), and never `make lerobot-image` in M3.

## 8. Risks

- **Slice identity is not full-corpus identity.** The `scene-0061` slice is its own content,
  so its unit ids differ from the full-mini snapshot's (ADR §3: units follow bytes). The M3
  reference contract is per slice; M5 regenerates it for the full snapshot.
- **`+` in object keys.** nuScenes paths contain `+` (`…+0800__CAM_FRONT__…`); key handling
  on real MinIO is an S1 acceptance item.
- **AV1 decoding** remains unverified; M3 stores frame selectors only.
- **LeRobot coverage.** One data file per dataset in both probes; multi-file / multi-chunk and
  multi-task episodes are handled by the reader rules but unprobed (OQ-8).
- **Licences.** nuScenes is non-commercial; its local copy is already ignored by git and stays
  local. pusht and aloha are MIT.
- **Registration read volume.** First registration re-hashes every file (0.48 GiB slice,
  5.05 GiB full mini); not measured.

## 9. Decisions (2026-10-22)

The Ready recommendations (OQ-1, OQ-2, OQ-3, OQ-5, OQ-9, OQ-10 and D1–D14 apart from D3,
D8 and D9) were accepted as written. They were checked against ADR-015 and the data in §2.
Three needed ADR text to follow them, and ADR-015 now carries that text:
- OQ-9 replaces the §6.2 "unless the config declares `unix_epoch`" clause and the §15 "every
  recording build config gains an explicit origin" consequence.
- D2 adds unit selection to §6.7.
- D5 adds the presence check for content files to the §6.4 gate.

The Review items were decided as follows.

**OQ-2a — amended.** `fps` is stored once, on an optional EpisodeManifest
`frame_timeline {clock, fps, frame_count}`, and not on `ClockDomain`. A clock domain decides
comparability (what zero means); a frame rate describes how one unit sampled that clock.
Putting the rate on the clock would make two episodes at different rates on
`lerobot.frame_time` differ in their clock declaration, though their comparability rule
(`unit` origin) is the same. The rest is as recommended:
- Frame indices are deterministic: `k` is the one integer with
  `t_ns = round_half_even(k · 10⁹ / fps)`, and the manifest validator checks every
  occurrence on the timeline's clock.
- No per-occurrence `timestamp` / `frame_index` value is stored.
- `lerobot.frame_time` has resolution 1 ns.
- `fps` is a positive integer, as in LeRobot `info.json`. A rational rate is not added
  without a source that needs it.

**OQ-6 — decided, anchor refined.** Native labels are imported in S6. A nuScenes annotation
belongs to a sample, so its anchor preserves the sample token:
- The anchor is `(content, content_id, LIDAR_TOP, nuscenes.timestamp, sample.timestamp)` with
  `group_ref = sample.token`.
- `LIDAR_TOP` is chosen because `sample.timestamp` equals its keyframe reading on 404 / 404
  samples (§2.3). The label reader requires this for every imported sample and fails
  otherwise.
- Resolution is defined on the Scene manifest: the one observation at the anchor, which must
  belong to the keyframe group whose `native_ref` equals `group_ref`. A mismatch is an error,
  not an unattached label.
- Import never reads a sample view and never chooses a camera image. A sample view later
  attaches labels through its own members, as today. A resolved label that no sample holds
  is counted as unattached, and the label set is unchanged.
- `render-labels` keeps producing RobotRun-anchored labels without `group_ref`.

**D3 — decided as recommended.** Format builders assign `SourceFileRef.media_type` from
source-format evidence. The inventory stays format-independent.

| Format | Evidence | Media type |
| --- | --- | --- |
| nuScenes camera | `sensor.modality = camera`, `.jpg` | `image/jpeg` |
| nuScenes lidar | `lidar`, `.pcd.bin` | `application/x.nuscenes.lidar-pcd-bin` (the existing identifier) |
| nuScenes radar | `radar`, `.pcd` | `application/x.nuscenes.radar-pcd` |
| LeRobot video | feature `dtype: video`, `.mp4` | `video/mp4` (the codec stays in `info.json`) |

Evidence that disagrees, such as a camera reading with another suffix, fails the build.
Registration checks `path`, `size_bytes` and `sha256` against the inventory. The media type
is a manifest assertion, covered by the manifest checksum.

**D8 — decided as recommended.** `EpisodeField.component_names` is optional. It holds the
source's dimension names in source order, and every value of that field has exactly that
length. pusht `observation.state` / `action` →
`["motor_0", "motor_1"]`.

**D9 — amended.** `next.*` features are typed per-frame transition signals, not events:
- **Role:** a new Episode stream role `transition` with its own occurrence list.
- **Occurrences:** one per frame, at the frame the row is recorded on (never shifted to the
  next frame), with values in their source type: bool for `next.done` / `next.success`,
  float32 widened exactly for `next.reward`.
- **No event derivation in the manifest.** Measured on pusht: `next.done` is true on the
  *last two* frames of every one of the 206 episodes (412 rows), `next.reward` is positive
  on 20,725 / 25,650 rows, and `next.success` is never true. An "episode ended" event derived
  from `next.done` would therefore be wrong for this source.
- **Downstream events:** an event view is derived later (alignment, learning export) only
  with a declared rule.
- **Task events stay events (D13).** They are the one event derivation in M3, because a task
  change is an event and the per-frame `task_index` is recoverable from them over the dense
  frame timeline.

**Open questions retained.** OQ-7 stays open until M5, because it needs the full mini
snapshot. OQ-8 stays open: v2.x is rejected, and multi-file / multi-task episodes stay
unprobed.
