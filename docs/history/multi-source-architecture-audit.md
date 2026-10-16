# Multi-source architecture audit (M1)

> Point-in-time audit of how far the implementation is from a multi-source robotics data
> platform. It records what the code does at the audited commit; it is not a current
> contract. The proposed target is
> [ADR-015](../adr/015-multi-source-platform.md), the source comparison is
> [Multi-source dataset probe](./multi-source-dataset-probe.md), and the work breakdown is
> [Multi-source implementation plan](./multi-source-implementation-plan.md).

```text
date     2026-10-16
branch   refactor/multi-source-platform
HEAD     bfd077e docs: update readme
scope    read-only: sceneops-core, -db, -recording, -scenes, -episodes, -derived,
         -analytics, apps/api, apps/worker, integrations/dataset-acquisition,
         integrations/lerobot, docs/architecture, ADR-007
```

Labels used below:

```text
VERIFIED   read in the code or measured at the audited HEAD; a path is given
INFERRED   follows from verified facts but was not executed
```

## 1. Summary

1. **RobotRun is the only ingress, by decision and by structure.**
   [ADR-007](../adr/007-canonical-ingestion-architecture.md) Amendment A4 (§29.2,
   invariants I-31, I-36) made a registered RobotRun recording the only source of
   canonical Scenes and Episodes and moved external datasets into a tool that emits MCAP.
   The requested target reverses decisions 1, 3, 4 and 5 of §29.2. The coupling is not a
   naming issue: it is in unit identity, the producer fingerprint, the database schema,
   registration scope, payload ownership, label anchors and analytics columns (§3).
2. **External datasets reach the platform only through a lossy conversion.** For nuScenes
   `scene-0061` the conversion drops all radar observations, five of eight CAN extracts,
   keyframe (sample) structure and every native token, and adds two synthetic mission events
   (§4, measured in the [probe](./multi-source-dataset-probe.md)). LeRobot has no inbound
   path at all; it is export-only.
3. **DatasetVersion is not a composition of pinned revisions.** Scene and Episode identity
   includes `(dataset_id, dataset_version)`, membership is a column of the unit row, a
   registration can repoint or delete members of an existing version, and manifests are
   stored under a DatasetVersion path. A DatasetVersion therefore holds "whatever revision
   is current", not a frozen set (§5).
4. **Temporal alignment exists twice, both domain-bound.** Episode alignment
   (`sceneops_core.episodes.alignment`, microseconds, fixed-frequency timeline) and Scene
   sample-view association (`sceneops_core.sample_views.build`, nanoseconds, anchor-channel
   timeline) implement nearest / previous association independently. Neither accepts a
   RobotRun or the other domain (§6).
5. **Most of the platform below and above the coupling is reusable unchanged**: source-time
   primitives, ProducerInfo and the fingerprint discipline, canonical JSON, write-once
   storage, revision pins for derived artifacts, the registrar-under-row-lock pattern, run
   records pinned to a revision, Jobs / Pipelines and the export adapter contract (§7).
   ADR-007 A2 / A3 already designed locator-free external provenance
   (`ExternalUnitSource`, `ExternalSourceRevision`); A4 removed it for lack of a producer,
   not for a design flaw (§29.9).

## 2. Current architecture as implemented

```text
EXTERNAL DATASET           integrations/dataset-acquisition (no SceneOps imports)
  nuScenes  ──────────▶    MCAP (ROS 2 messages) ──┐
  LeRobot   (no path)                              │
                                                   ▼
ACQUISITION                Publisher ─▶ RobotRunManifest + MCAP ─▶ REGISTER_ROBOT_RUN
  robot ─▶ bridge ─▶ Kafka ─▶ Capture ─┘                             │
                                                                   ▼
CANONICAL                  RobotRun ──▶ BUILD_RECORDING_SCENES ──▶ REGISTER_SCENES
(per DatasetVersion,                ──▶ BUILD_RECORDING_EPISODES ─▶ REGISTER_EPISODES
 per robot_run_id scope)
                                                                   ▼
DERIVED                    Scene  ─▶ LabelSet / SceneSampleView ─▶ ScenarioSet ─▶ prediction ─▶ evaluation
                           Episode ─▶ AlignedEpisode ─▶ LearningDataExport ─▶ curation / LeRobot export
```

VERIFIED against [overview.md](../architecture/overview.md) §2–§3,
`packages/sceneops-core/sceneops_core/pipelines/builtin.py` (four PipelineTypes; both
canonical pipelines take `robot_run_id`) and the job registry.

## 3. RobotRun coupling inventory

Every row is VERIFIED at the cited path. "Blocks" says which target requirement the row
violates: **S** = Scene / Episode must not require a RobotRun parent, **N** = native source
data must not be converted to a common physical format, **V** = DatasetVersion must compose
pinned revisions.

| # | Location | What is coupled | Blocks |
| --- | --- | --- | --- |
| C1 | `sceneops_core/provenance/sources.py` | `RecordingSegmentSource` is the only source block; it requires `robot_run_id` and checks `recording_artifact_id == robot_run_recording_artifact_id(robot_run_id)` | S |
| C2 | `sceneops_core/provenance/identity.py` | `canonical_unit_id` hashes `{domain, dataset_id, dataset_version, source: {source_kind, robot_run_id, unit_key}}` | S, V |
| C3 | `sceneops_core/provenance/producer.py` | `compute_producer_fingerprint` accepts only `RecordingSourceRevision` (`robot_run_id`, `recording_checksum`) | S |
| C4 | `sceneops_core/scenes/schemas/manifests.py`, `episodes/schemas/manifests.py` | `SceneLineage.source` / `EpisodeLineage.source` typed `RecordingSegmentSource` | S |
| C5 | `sceneops_db/models/scenes.py`, `models/episodes.py` | `robot_run_id` NOT NULL, FK `robot_runs` RESTRICT; `(dataset_id, dataset_version)` FK on the unit row | S, V |
| C6 | `apps/worker/sceneops_worker/scenes/registration.py` (`_check_input_scope`, `_verify_recording_source`, `_apply_recording`); `episodes/registration.py` | registration scope is `(DatasetVersion, robot_run_id)`; manifests must have been built from that RobotRun's recording checksum; `replace=True` deletes and repoints members | S, V |
| C7 | `sceneops_recording/observations/payloads.py`, `observations/store.py` | `OBSERVATION_PAYLOAD` artifact id = sha256 of `(robot_run_id, topic, channel_index, extraction)`; key `{payload_root}/{robot_run_id}/{artifact_id}`; owned by the RobotRun | S, N |
| C8 | `sceneops_core/labels/schemas.py` (`ObservationAnchor`) | a label anchor is `(robot_run_id, channel, source_clock, timestamp_ns)` | S |
| C9 | `sceneops_core/sample_views/build.py` (label index) | anchors whose `robot_run_id` differs from the Scene's source RobotRun are rejected | S |
| C10 | `sceneops_analytics/tables.py` | `scenes` / `observations` / `keyframes` analytics tables carry a `robot_run_id` column | S |
| C11 | `sceneops_scenes/artifacts.py`, `sceneops_episodes/artifacts.py` | canonical manifests are stored under `{dataset_root}/{dataset_id}/versions/{version}/…/{unit_id}/manifest-<sha256>.json` | V |
| C12 | `sceneops_core/jobs/schemas/params/{scene,episodes}.py`, `pipelines/builtin.py` | `BUILD_RECORDING_*` take `dataset_id`, `dataset_version`, `robot_run_id`, `build_config`; downstream jobs address a DatasetVersion | S, V |
| C13 | `apps/api/app/domains/{scenes,episodes}/router.py` | list filters by `robot_run_id`; no source-neutral filter | S |
| C14 | `sceneops_core/integration_runtime/enums.py` | `IntegrationOperation` has only `EXPORT`; `ExternalDatasetRef` is a locator for export only | N |
| C15 | `integrations/dataset-acquisition` (`nuscenes.py`, `labels.py`) | the only external-dataset path converts to ROS 2 messages in an MCAP; nuScenes labels are re-anchored on the resulting RobotRun | N |

Coupling that is **correct and stays** (it describes acquisition, not canonical units):
`RobotRunManifest`, `RobotRunRecord`, `resolve_recording`, the capture / publication /
reconciliation machinery, `ingest_robot_states` (a RobotRun telemetry projection), and the
Scene / Episode recording builders themselves. The target keeps RobotRun as the acquisition
identity; it removes RobotRun only as the *mandatory* parent of canonical units.

## 4. External datasets today

VERIFIED in `integrations/dataset-acquisition/src/dataset_acquisition/nuscenes.py` and the
reference corpus log `data/reference/nuscenes-mini-v1/prepare-log.jsonl`; counts measured in
the [probe](./multi-source-dataset-probe.md) §2.

```text
nuScenes scene-0061             native                    reference MCAP
camera sample_data (6 ch)       1,318                     1,318 CompressedImage + 1,318 CameraInfo
lidar sample_data               382                       382 PointCloud2
radar sample_data (5 ch)        1,263                     0           "Radar is not converted."
keyframes (sample)              39 (2 Hz)                 0           no keyframe flag or grouping
CAN extracts                    8 files                   3 converted (pose, ms_imu, vehicle_monitor)
/vehicle/control                —                         38 msgs derived from vehicle_monitor (≈1.9 Hz)
/mission/status                 —                         2 synthetic events (running / completed)
native tokens                   every row                 none ("The recording carries no … nuScenes token")
annotations                     on keyframes              separate label path, anchored on the RobotRun
```

Consequences:

- Structure the source defines (keyframe groups, sample / instance tokens, radar) is lost
  before canonicalization and cannot be recovered from the RobotRun.
- Payload bytes exist three times for a converted dataset: the native files, the MCAP, and
  the extracted `OBSERVATION_PAYLOAD` artifacts.
- Source identity is replaced by an acquisition identity (`robot_run_id`); a nuScenes scene
  is reachable only through the fixture → RobotRun mapping of the reference corpus.
- LeRobot: `LeRobotDatasetAdapter` is an export writer
  (`sceneops_analytics/external_adapters/lerobot/`). There is no reader, no import job and
  no `IntegrationOperation.INGEST`. A4 §29.13 assigned LeRobot import to the acquisition tool
  (one episode → one RobotRun); it was never implemented.

## 5. DatasetVersion today

VERIFIED in `sceneops_db/models/datasets.py`, [data-model.md](../architecture/data-model.md)
§2 and the registrars.

- A DatasetVersion row holds identity and cached summaries (`scene_count`,
  `episode_count`, …). It has one status, `registered`, and no frozen state.
- A version is created **empty** and filled later: `POST /datasets/{id}/versions`
  (`apps/api/app/domains/datasets/router.py`) and `DatasetStore.create_version`
  (`apps/worker/sceneops_worker/stores/datasets.py`) create the row before any member exists,
  and each `REGISTER_*` adds members afterwards. In effect every version is a persistent
  draft that is never closed.
- Membership is the unit row itself: a SceneRecord / EpisodeRecord *belongs to* one
  DatasetVersion because its id is derived from it (C2). The same source unit in two
  versions is two units with two ids and two stored manifest copies (C11).
- A registration with `replace=True` deletes members absent from the new set and repoints
  reused ids to a new manifest revision inside an existing DatasetVersion. A version can
  therefore change content after other workflows read it. Derived workflows survive this
  only because they pin manifest checksums themselves (ADR-007 §18.5).
- Concurrency correctness relies on `SELECT … FOR UPDATE` on the DatasetVersion row
  (`PostgresDatasetVersionRepository.lock_for_update`), proven by a two-session test. This is
  needed *because* versions are mutable.

INFERRED: a target in which a DatasetVersion pins revisions and units exist independently of
any version removes the need for that lock on the version row, but requires a new
membership table and a separate unit-revision registry.

## 6. Temporal processing today

| Aspect | Episode alignment | Scene sample views |
| --- | --- | --- |
| Module | `sceneops_core/episodes/alignment/` (`engine.py`, `policies.py`, `timeline.py`) | `sceneops_core/sample_views/build.py` (`_associate`) |
| Input | one `EpisodeManifest` revision | one `SceneManifest` revision + label sets |
| Time unit | ns floored to **µs** | **ns** |
| Timeline | fixed frequency (`target_frequency_hz`) over the Episode window | anchor-channel observations with stride |
| Policies | exact, nearest, previous, linear interpolation | nearest, previous |
| Clock rule | one alignment clock; streams on any other clock rejected | association never crosses clocks |
| Output | `AlignedEpisode` (observation / action namespaces, task, outcome) | `SceneSampleViewManifest` (members, pose, labels) |

VERIFIED observations:

- `ChannelSample(timestamp_us, payload)` and the `apply_*` functions in `policies.py` are
  already stream-agnostic; the Episode coupling is in `engine.py` (reads
  `EpisodeStreamRole`, builds observation / action channels).
- **Source timestamp normalization is done by the builders, not by alignment**: each
  recording build config declares a per-channel time policy (`header_stamp`, `log_time`,
  `publish_time`, `payload_field`) and a clock identifier, and the manifest stores integer ns
  per declared clock. This separation is correct and is the one the target asks for; it is
  implemented per builder and is RobotRun-specific today.
- No RobotRun-scope alignment exists. `ingest_robot_states` is a fixed-topic telemetry
  projection, not an aligned view.
- **A clock is an identifier only.** `validate_source_clock`
  (`sceneops_core/common/identifiers.py`) checks an open identifier (external clocks are
  `<format>.<clock>`). Nothing declares whether a clock is absolute (epoch), absolute only
  within one source, or relative to the unit's own origin. Every clock in use today is
  recording-derived, so the gap is latent. A relative clock (LeRobot episode time) would
  look comparable across units and with epoch clocks.
- **The Episode timeline drifts for some rates.** `generate_fixed_frequency_timeline`
  (`episodes/alignment/timeline.py`) rounds the period once to integer µs and emits
  `start + n · dt_us`. At 30 Hz `dt_us` = 33,333, so tick n is n/3 µs off the nominal
  grid. Aligning a 30 fps source at its own rate would therefore not resolve exactly.
- **Absolute time is assumed downstream.** `AlignedEpisode.source_start_timestamp_us` /
  `source_clock` and the LeRobot adapter's `TIMESTAMPS → LOSSY_EXPLICIT` reasoning ("the
  absolute source-clock anchor … is not represented") both presume an absolute anchor
  exists ([dataset-interoperability.md](../architecture/dataset-interoperability.md) §5).

Known defects in the learning path, VERIFIED, relevant to M4 and not fixed here:

- `AlignedValue.reference_uri` is never set by production code (the engine sets
  `reference_metadata`), and `learning_signals` persists only `reference_uri` /
  `reference_modality` (`sceneops_analytics/learning_tables.py`), so image references are
  lost at export.
- `AlignedEpisode.task` / `outcome` are never set (`apps/worker/sceneops_worker/jobs/dataset/align_episode.py`
  has no task handling); LeRobot export depends on `default_task` in its config.
- Microsecond flooring makes alignment lossy relative to canonical ns timestamps.

## 7. Reusable components

| Component | Path | Reuse in target |
| --- | --- | --- |
| Source time | `sceneops_core/provenance/source_time.py` | unchanged; `promote_to_ns` already covers µs (nuScenes) |
| ProducerInfo / fingerprint | `sceneops_core/provenance/producer.py` | unchanged discipline; source input becomes a union of revision types |
| Canonical JSON, checksums, derived ids | `sceneops_core/common/` | unchanged |
| Write-once storage | `sceneops_storage/write_once.py` | unchanged; used for snapshot files |
| Recording publication pattern | `sceneops_recording/publisher.py`, `publish_pending.py` | template for DB-free external snapshot publication (manifest written last) |
| Registrar pattern | `apps/worker/sceneops_worker/{scenes,episodes}/registration.py` | verify-then-apply in one transaction; scope rule changes |
| Revision-pinned run records | `scene_run_records`, `episode_run_records` | unchanged; already keyed on `manifest_artifact_id` + checksum |
| Derived pins | `sceneops_derived/manifests.py`, `sceneops_core/common/derived_ids.py` | unchanged |
| Association kernels | `sceneops_core/episodes/alignment/policies.py` | lifted into the shared alignment capability |
| Export adapter contract, semantic-loss classification | `sceneops_analytics/external_adapters/` | export unchanged; the `LOSSLESS / LOSSY_EXPLICIT / UNSUPPORTED` vocabulary is reusable for import mapping reports |
| `ExternalDatasetRef` | `sceneops_core/integration_runtime/external.py` | stays a locator; never provenance |
| ADR-007 A2 / A3 designs | §27.2, §28 | `ExternalUnitSource` / `ExternalSourceRevision` shape and the locator-free rule are the starting point for the content source binding (ADR-015 `ContentUnitSource`); A2's revision fields become the SourceSnapshot declaration |
| Negative-control verifier pattern | `e2e-streaming-equivalence` ([test-matrix.md](../development/test-matrix.md)) | template for the independent contract verifier (dropped message, 1 ns shift, changed payload checksum) |

## 8. Conflicting assumptions

| Assumption in current code / ADRs | Target requirement | Resolution proposed in ADR-015 |
| --- | --- | --- |
| I-31: every canonical unit has a `RecordingSegmentSource` | Scene / Episode independent of RobotRun | source binding becomes a closed union: recording segment or external unit |
| I-36: external tooling produces only MCAP / ROS 2 input | external datasets keep native data | a Dataset Integration path registers native snapshots; the acquisition tool stays as an acquisition simulator |
| §29.1 "two producers per domain means two mappings to keep equivalent" | multiple sources per domain | accepted cost, bounded by per-format builders and a source-independence test (I-20 kept) |
| §29.1 "format knowledge leaks into core" | native source references | format is an open identifier on the snapshot record (and qualifies source clocks, as `validate_source_clock` already allows); readers live in a capability package; no format enters unit identity |
| unit identity scoped to DatasetVersion (C2) | DatasetVersion composes pinned revisions | separate identities for snapshot, unit, revision and DatasetVersion; membership pins revisions |
| `replace=True` mutates a DatasetVersion; versions are created empty | versions are reproducible | registration is additive per revision; a version is composed inside one Job and committed complete; a changed composition is a new version |
| alignment in µs (Episode) vs ns (Scene) | one source-independent capability | one ns kernel; domain outputs stay separate |
| a clock is an identifier only; absolute time assumed downstream | relative timestamps without absolute time | declared clock domains (origin `unix_epoch` / `source` / `unit`); alignment within one domain instance |

## 9. Risks found during the audit

- **Golden reference contract.** [reference-contract.md](../development/reference-contract.md)
  fixes 20 RobotRuns, 20 Scenes and 20 Episodes by identity. Any change to unit identity (C2)
  changes every Scene / Episode id and every derived pin. The contract, baselines and
  `e2e-cleanroom` expectations must change in the same change. Today's acceptance for them
  is reconstruction and counts (20 / 20 / 20). Simply re-running the bootstrap after an
  identity change would accept the new output as its own reference: a semantic error that
  keeps the counts would pass. The regenerated contract needs an independent check against
  raw inputs (ADR-015 §12).
- **Object count.** Recording builds already write one `OBSERVATION_PAYLOAD` ArtifactRecord
  per extracted message. Registering native files one ArtifactRecord per file has the same
  order of growth (nuScenes mini: 31,206 `sample_data` files; full nuScenes is about two
  orders of magnitude larger, not measured).
- **Isolation.** The LeRobot SDK needs `numpy>=2` and is isolated in `integrations/lerobot`.
  A native LeRobot reader must not pull it into the workspace lock
  ([Dataset interoperability](../architecture/dataset-interoperability.md) §5).
- **Raw-source boundary.** `make check-runtime-boundary` allows `/data/raw` only in
  acquisition / reference-preparation services. Native snapshot publication needs the same
  exemption, scoped the same way.
