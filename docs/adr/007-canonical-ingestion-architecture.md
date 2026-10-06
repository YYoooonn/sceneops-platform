# ADR-007: Canonical Ingestion Architecture

## Status

**Accepted — implemented. Closed by Amendment A9 (§34).**

This ADR froze the *target* ingestion architecture for SceneOps. At the time
of acceptance almost none of the target contract was implemented; the amendments
below record each implementation step, and A9 records the final workflow surface
and closes the ADR. Throughout this document three labels are used wherever
confusion is possible:

```text
CURRENT IMPLEMENTATION    what the repository does at the audited HEAD
TARGET CONTRACT           what this ADR freezes; implementation must converge on it
DEFERRED                  explicitly out of scope; a direction, not a contract
```

Unlabelled normative statements ("must", "never", "is") describe the
**TARGET CONTRACT**. They must not be read as descriptions of current
behavior.

Audit basis:

```text
branch     refactor/domain-ingestion-architecture
HEAD       4123d33 fix(robots): require verified recording artifact for Episode materialization
date       2026-10-02
```

**Amendment A1 — source-faithful canonicalization and external-format
extensibility.** Accepted. A1 freezes two principles that ADR-007 implied but
did not state: canonical units preserve source observations within their
selected domain boundary, and lossy or workflow-specific transformations
are derived (§13.5–§13.12). It also states that external formats are
integration implementations, not core domain types (§20.3–§20.7). A1 adds
§1.4, §6.7, §13.5–§13.12 and §20.3–§20.7, invariants I-21–I-25, and an
amended implementation sequence (§24). It also makes clarifying edits where
earlier text treated sampling or channel renaming as canonical build steps.
It changes no decision about RobotRun, registration, identity, replacement
or DatasetVersion. Its "CURRENT IMPLEMENTATION" notes were audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       1663922 feat(robots): implement recording publication and registration
date       2026-10-02
```

"CURRENT IMPLEMENTATION" notes outside A1's sections still describe HEAD
4123d33 (§23, rule 7).

**Amendment A2 — source and provenance contract freeze (implementation step
4).** Accepted. A2 closes the open questions that the Scene and Episode
contract steps depend on: source timestamp precision and clock semantics,
channel-boundary semantics, the nuScenes keyframe/sweep boundary,
recording-derived payload ownership, external format identifiers,
integration resolution, and the exact `ProducerInfo` / producer-fingerprint
contract (§27). It adds §27 and invariants I-26–I-29. It refines the TARGET
text of §13.9, §14.1, §15.2 and §20.5 in place to match, annotates §13.12
item 6, and moves three step-4 items to step 5 (§27.1). It changes no decision about RobotRun,
registration, identity, replacement or DatasetVersion. Its "CURRENT
IMPLEMENTATION" notes were audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       ce56f8e fix(robots): preserve RobotRun provenance on Robot deletion
date       2026-10-02
```

**Amendment A3 — locator-free canonical source provenance (implementation
step 5).** Accepted. A2 placed the whole `ExternalDatasetRef`, including its
`uri`, inside `ExternalUnitSource`, so one dataset revision mounted at two
paths produced two different canonical manifests. A3 separates the
integration-side **locator** and display name from canonical
**provenance**: canonical external provenance carries only the
`ExternalSourceRevision` and the `source_unit_key`, never a location or a
display name (§28). It
refines the TARGET text of §13.9, §14.1, §14.3, §20.2, §20.4 and §27.2 in
place, adds invariant I-30 (§21) and adds §28. It changes no
decision about identity, the producer fingerprint, recording provenance,
registration, replacement or DatasetVersion. Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       d378bda feat(core): freeze canonical source and provenance contracts
date       2026-10-03
```

**Amendment A4 — acquisition-first architecture.** Accepted. A4 changes the
platform ingress. Raw acquisition becomes the only ingress for acquired
sensor and robot-state data: an immutable recording, registered as a RobotRun. Streaming and batch
acquisition both converge on it. Scenes and Episodes are produced only by
batch canonicalization of registered recordings. External datasets are no
longer canonical sources. They become acquisition test data, produced by a
standalone tool outside the platform. A4 supersedes the *external* half of
the `{external, recording} × {Scene, Episode}` taxonomy (§2, §5, §6.1, §6.6,
§6.7, §13.9 external part, §17.1, §17.2, §18.2, §20.3–§20.7, §27.4, §27.6,
§28) and replaces implementation steps 6–11 (§24). It freezes the L1
raw-recording contract, and adds §29 and invariants I-31–I-37. It changes no
decision about any of these: recording publication, RobotRun registration,
the verified resolver, the identity of recording-derived units, the producer
fingerprint, Scene/Episode separation, registrar ownership, replacement, or
the canonical/derived boundary. Superseded text is kept and marked in place,
and §29.17 lists every affected section. Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       1406cb2 fix(db): add missing robot_states.robot_run_id index
date       2026-10-04
```

**Amendment A5 — recording Scene canonicalization (implementation step 7).**
Accepted. A5 records the step-7 decisions A4 left open: Q4, the segment
window clock (§30.2), and Q2, the canonical lidar payload (§30.4). It
freezes the v1 recording Scene builder contract (§30.1–§30.8). Q4 amends
§27.2: a `RecordingSegmentSource` window is in the segmentation clock the
producer's build configuration declares, no longer a clock copied from the
RobotRun. It makes I-35 precise: semantic equivalence compares canonical
content, not provenance-owned identity such as payload artifact ids (§30.9).
A5 adds §30 and invariants I-38 and I-39. It changes no decision about
identity, the fingerprint definition, registrar ownership, replacement or
the canonical/derived boundary. The serialized bytes of recording
provenance, the fingerprint and unit ids are unchanged (§30.6). Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       6fe13bf feat(acquisition): implement containerized L1 batch acquisition
date       2026-10-04
```

**Amendment A6 — recording Episode canonicalization (implementation step 8).**
Accepted. A6 freezes the canonical Episode: a source-faithful,
task/behavior-oriented projection of one RobotRun that keeps observation,
state, action and event streams asynchronous, each on its own declared
clock (§31.2–§31.6). It freezes the `RecordingEpisodeBuilder` boundary and
build configuration (§31.1, §31.3), Episode segmentation and window
semantics (§31.4), the no-alignment invariant and the Episode ↔
`AlignedEpisode` boundary (§31.8), recording-only provenance, identity and
unit keys (§31.9–§31.10), and registration (§31.11). It applies §30.2 (window
clock) to Episodes, amends §13.1 and §17.4 in place (no canonical control
frequency or outcome), records the payload sharing between Scene and Episode
builds (§31.6) and adds invariants I-40–I-43. It changes no decision about
the fingerprint definition, unit-id schema, registrar ownership, replacement
or DatasetVersion. Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       562f890 feat(scenes): build canonical Scenes from registered recordings
date       2026-10-04
```

**Amendment A7 — streaming acquisition and batch/streaming equivalence
(implementation step 9).** Accepted. A7 freezes the production streaming path
(replay or robot → ROS 2 → bridge → Kafka → capture → L1 MCAP → Recording
Publisher → `REGISTER_ROBOT_RUN`) and resolves what A4 left to step 9: capture's
timing and sequence semantics (R4, R6, R11), the sensor / `tf` / `CameraInfo`
channel gap (R9), the ROS 2 replay sink, and open question Q3, Kafka transport of
large sensor messages (§32). It records one transport defect the real vertical
exposed, DDS alignment padding in received payloads (§32.4), and the rule that
removes it. It adds §32 and invariants I-44–I-47. It changes no decision about
Scene or Episode canonicalization, RobotRun, registration, identity, the
fingerprint or DatasetVersion. Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       7c15825 feat(episodes): build canonical Episodes from registered recordings
date       2026-10-05
```

**Amendment A8 — derived L3 layer: labels, sample views, curation, detection
and learning export (implementation step 10).** Accepted. A8 completes the
derived layer on top of canonical Scenes and Episodes without changing either
(§33). It decides Q1: labels are separate, lineage-bearing data anchored on
canonical observations, never part of a Scene, an Episode or a RobotRun
(§33.2). It defines `SceneSampleView`, the policy-driven synchronization and
association layer perception needs (§33.3), and refactors ScenarioSet,
detection inference and evaluation to consume explicit, checksum-pinned
derived revisions instead of a DatasetVersion scan (§33.4, §33.5). It finishes
`AlignedEpisode` identity (§33.6) and records what learning export is and is not
(§33.7). It adds §33, invariants I-48–I-55 and a DB migration, and removes the
stale nuScenes-era derived workflows (§33.8). It changes no decision about
RobotRun, Scene or Episode canonicalization, identity, the fingerprint,
registration, replacement or DatasetVersion. Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       72c5aab feat(streaming): acquire sensor-bearing recordings by ROS2 replay and prove batch equivalence
date       2026-10-05
```

**Amendment A9 — final workflow surface and ADR closure (implementation
step 11).** Accepted. A9 consolidates SceneOps into the final surface and closes
this ADR (§34). Exactly four Pipelines remain (`RECORDING_SCENE_BUILDING`,
`RECORDING_EPISODE_BUILDING`, `SCENE_ML_EVALUATION`,
`EPISODE_LEARNING_DATA_BUILDING`); every other operation is an atomic Job.
Exactly five E2E journeys remain; infrastructure behavior moves below them
into pipeline-contract and infrastructure tests. `canonical-bootstrap` is
rebuilt as L1/L2-only developer orchestration and `e2e-cleanroom` as the
fresh-state acceptance on top of it. A9 also lands the schema debt earlier steps
deferred: SceneManifest v2 (no annotations), DatasetVersion without channel
requirements or metadata, dead contracts, ONNX, and nuScenes-named telemetry and
input naming. It changes `ALIGN_EPISODE` to take several pinned Episodes (§34.4)
and adds an explicit `default_task` to the external export config. It adds §34,
invariants I-56–I-60 and a DB migration. It changes no decision about RobotRun,
registration, identity, the fingerprint, Scene or Episode canonicalization
(except removing embedded annotations from the Scene schema), replacement or the
derived layer's pin rules. Audited at:

```text
branch     refactor/domain-ingestion-architecture
HEAD       26b9df0 feat(derived): pin labels, views, inference, evaluation and exports
date       2026-10-05
```

Relationship to earlier ADRs:

- [ADR-001](./001-postgresql-operational-metadata.md),
  [ADR-002](./002-object-storage-for-assets.md),
  [ADR-006](./006-parquet-for-analytical-data.md): unchanged. This ADR applies
  their PostgreSQL / Object Storage / Parquet responsibility split to ingestion.
- [ADR-005](./005-ros2-vs-kafka-boundary.md): unchanged. This ADR defines what
  happens *after* the ROS2 → Kafka → capture boundary that ADR-005 owns.
- [ADR-003](./003-batch-first-architecture.md): unchanged and applied by A4.
  Streaming is an acquisition mode only. Canonicalization is always a
  batch job over a registered recording (§29.4).

When this ADR is implemented, the active documents in `docs/architecture/`
(`data-model.md`, `scene-domain.md`, `episode-domain.md`,
`jobs-and-pipelines.md`, `streaming-transport.md`,
`external-integration-runtime.md`, `storage-layout.md`,
`reserved-and-limitations.md`) must be updated to describe the then-current
system. This ADR does not replace them.

---

## 1. Context

SceneOps grew its ingestion paths one phase at a time. Each path was validated
on its own terms, but they do not share one model of *what a canonical unit
is*, *where it came from*, or *who is allowed to write it*.

### 1.1 Observed problems (CURRENT IMPLEMENTATION)

**RobotRun conflates runtime state, provenance, and dataset membership.**
`RobotRunModel` (`packages/sceneops-db/sceneops_db/models/robots.py`) carries
`status` (`RobotRunStatus`: `recording` / `completed` / `ingested` / `failed`),
`dataset_id`, `dataset_version`, `raw_log_id`, `rosbag_uri`, `mcap_uri` and a
free-form `metadata` JSONB. Two creation paths exist:

```text
POST /robot-runs  (apps/api/app/domains/robots/router.py)
sceneops-worker robots register-run
    → metadata-only RobotRun; mcap_uri stored as-is; no artifact, no checksum

sceneops-worker robots register-capture
    → register_robot_run_capture()  (apps/worker/sceneops_worker/robots/registration.py)
    → uploads local MCAP, verifies, creates ArtifactRecord + RobotRun(status=completed)
```

The verified path is correct but lives in the worker and *uploads* bytes as
part of DB registration, so publication and registration are one operation
owned by a DB-bound process. The metadata-only path can create a RobotRun
that no verified recording backs. HEAD 4123d33 made Episode building refuse
such RobotRuns (`RobotRunNotMaterializedError`), but the path still exists.

**Recording consumers accept unverified URIs.** `BuildEpisodesJobParams` and
`IngestRobotStatesJobParams` both accept a bare `mcap_uri` alongside
`robot_run_id`. `materialization.py` has both an ArtifactStore branch and a
local-path branch.

**DatasetVersion owns things that are not membership.**
`DatasetVersionModel` carries `raw_source_root_uri`, `required_channels`,
`manifest_uri`, `source_dataset_id` / `source_dataset_version`, and a Scene
quality cache (`latest_validation_run_id`, `validation_status`,
`should_block_pipeline`, `validation_report_uri`, `latest_profile_run_id`,
`profile_report_uri`). `Dataset.type` is `DatasetType` =
`nuscenes` / `waymo` / `kitti` / `custom`, which is a source format.

**Scene status mixes registration and quality.** `SceneStatus` is
`created` / `built` / `validated` / `profiled` / `failed`; validate/profile
jobs mutate it. `EpisodeStatus` already holds registration lifecycle only
(`created` / `registered`), with readiness derived from run records. The two
domains disagree on the same concept.

**Canonical writes are not owned by one component.** `BuildScenesJobHandler`
writes DatasetVersion scene summaries (`_update_scene_summary_after_build`)
before any SceneRecord exists. The dataset API service also calls
`update_scene_summary`. `RegisterSceneJobHandler` silently `continue`s when a
manifest cannot be loaded, and so does `RegisterEpisodeJobHandler`.

**Manifests are mutable in place.** Scene manifests are written to
`{scene_manifest_root}/{scene_id}.json`. A rebuild overwrites the bytes that
an existing SceneRecord's `scene_manifest_uri` points to.

**Provenance is source-specific and untyped.** `SceneLineage` and
`EpisodeLineage` carry `raw_log_id`, `source_dataset_id`, and free-form
`metadata`. `SceneGenerationMethod` mixes source kind (`raw_log`, `dataset`)
with simulators (`carla`, `isaac_sim`). Nothing records *which producer
semantics and configuration* produced a unit, so a rebuild with a different
configuration cannot be told apart from a retry.

**Source-specific assumptions leak past canonicalization.** Examples:
nuScenes channel names (`CAM_FRONT`, `LIDAR_TOP`) in
`sceneops_core/constants/sensors.py`, `scenes/schemas/sampling.py`, and
detection params; `steering` / `throttle` / `brake` hard-wired as Episode
action fields in `episode_builder.py`; `mcap_log_time` as the only source
clock constant in `episodes/alignment/config.py`; scene frame URIs that point
into an external nuScenes dataroot.

**The fake raw-log path.** `RawLogSourceType.NUSCENES_RAW_LOG_MOCK` makes
nuScenes masquerade as a raw robot log so `BUILD_SCENES` has an input. It is
the default `source_type` in `build_scenes.py`. It does not represent a real
recording and has no Episode equivalent.

**External Episode ingestion does not exist.** The LeRobot integration is
EXPORT-only (`docs/architecture/external-integration-runtime.md` §6). No path
turns an external Episode dataset into `EpisodeRecord`s.

**Pipeline taxonomy is asymmetric.** Current `PipelineType` values are
`DATASET_SCENE_INGESTION`, `RAW_LOG_SCENE_BUILDING`,
`RAW_LOG_EPISODE_BUILDING`, `SCENE_REGISTRATION`, `SCENARIO_CURATION`, and
`DETECTION_EVALUATION`. There is no symmetric `{external, recording} ×
{Scene, Episode}` taxonomy.

### 1.2 What is already right and is preserved

- Scene and Episode are separate domain models with separate builders,
  registrars, run records and APIs.
- Integration runtimes (`sceneops_integrations.nuscenes`,
  `tools/lerobot-integration`) are DB-free, Celery-free processes that talk
  to the platform only through `IntegrationRequest` / `IntegrationResult`.
- Capture (`ros2/capture/`) is DB-free. `CaptureSession` lifecycle is
  in-memory runtime state (`SessionState`: `DISCOVERED → RECORDING →
  FINALIZING → FINALIZED | FAILED` in `ros2/capture/router.py`).
- Recording identity is deterministic
  (`sceneops_core.common.ids.robot_run_recording_artifact_id`), and
  registration retries converge by checksum.
- DatasetVersion summary mutation is serialized with a real
  `SELECT … FOR UPDATE` row lock
  (`PostgresDatasetVersionRepository.lock_for_update`) and summaries are
  recomputed rather than incremented (`RegisterEpisodeJobHandler`).
- Episode readiness is derived from run records, not from mutable canonical
  status.
- Execution-scoped, checksum-verified recording materialization
  (`materialize_recording`).

### 1.3 Refactor policy

This branch puts **a clean final architecture ahead of backward
compatibility**. Existing APIs, schemas, PipelineTypes, JobTypes, CLI
commands, E2E flows and DB fields are not constraints just because they
exist. Breaking schema changes, renames, deletions, E2E replacement and
dev-state reset are acceptable (§23). This policy takes precedence over the
general "smallest additive change" guidance in
`.claude/instructions/development-workflow.md` for the work this ADR scopes.
It does **not** relax correctness, failure-safety, idempotency or lineage
requirements.

### 1.4 Context for Amendment A1

ADR-007 fixed *who* writes canonical state and *how* it is identified. It did
not say *what* a canonical unit must preserve from its source. The audit at
HEAD 1663922 shows that canonical Scene construction is currently lossy and
source-shaped. Frames are kept only when a sampling step associates them
with an anchor-channel sample. Payload URIs point into an external dataroot.
One `channel` field carries nuScenes vocabulary, and detection is hard-wired
to it (§13.12). If the Scene refactor freezes that shape into new manifests,
every future workflow inherits one workflow's sampling choices, and each new
source has to imitate nuScenes. A1 sets the semantic requirements that the
later Scene and Episode contract steps must meet. It does not define those
schemas.

---

## 2. Decision

> **Amended by A4 (§29.2).** The `external` source kind, the two
> `EXTERNAL_*` pipelines and decision 14 are superseded. Every Scene and
> Episode is produced from a registered RobotRun recording, and external
> datasets enter only as acquisition test data. Decisions 1–13 stand.

SceneOps has exactly two canonical dataset units:

```text
SceneRecord      spatiotemporal environmental observation unit
EpisodeRecord    task / interaction / action-state trajectory unit
```

Each can originate from one of two source kinds:

```text
external    an already-structured external dataset that already contains the unit
recording   a continuous robot recording that must be segmented into units
```

This gives four stable ingestion pipelines:

```text
                    Scene                          Episode
external    EXTERNAL_SCENE_INGESTION       EXTERNAL_EPISODE_INGESTION
recording   RECORDING_SCENE_BUILDING       RECORDING_EPISODE_BUILDING
```

The governing decisions:

1. **Manifest / Record / Artifact are distinct.** A Manifest is a durable,
   serializable contract. A Record is a queryable PostgreSQL projection. An
   Artifact is physical bytes plus integrity and lineage metadata (§3, §7).
2. **Recording publication is database-independent and manifest-last.** A
   DB-free Recording Publisher uploads and verifies the MCAP, then writes a
   canonical `RobotRunManifest` last as the publication marker (§7, §8).
3. **`REGISTER_ROBOT_RUN(manifest_uri)` is the only way a RobotRun comes into
   existence.** It verifies and projects. It never uploads or moves bytes
   (§12).
4. **`RobotRunRecord` is immutable finalized provenance.** It has no
   lifecycle status, no dataset membership and no duplicate URI fields (§10).
   Runtime capture lifecycle stays in `CaptureSession` (§11).
5. **All recording consumers go through one verified resolver** keyed by
   `robot_run_id` (§12.4).
6. **Scene and Episode keep separate models and builders.** They share
   typed provenance building blocks, not a universal unit type (§13, §14).
7. **`producer_fingerprint` identifies build semantics** and drives
   replacement (§15, §18).
8. **DatasetVersion is canonical membership and nothing else** besides
   recomputed summary projections (§16).
9. **Registrars are the only writers of canonical Scene/Episode records,
   membership and DatasetVersion summaries** (§17.5).
10. **Manifests are immutable, write-once artifacts.** Records point at an
    explicit manifest revision (§19).
11. **After canonicalization, no downstream workflow may need to know the
    source** (§20).
12. **Legacy paths are removed, not shimmed** (§22, §23).
13. **Canonical units are source-faithful within their selected domain
    boundary.** Canonicalization selects boundaries and normalizes
    representation. Lossy and workflow-specific transformations are derived
    (§13.5–§13.11).
14. **External formats are integrations, not domain types.** A new external
    format adds an integration that maps onto the existing canonical
    manifests. It does not add a core type, a schema or a pipeline
    (§20.3–§20.6).

---

## 3. Terminology

### 3.1 The three kinds of thing

```text
Manifest   durable serializable data/source contract
           - immutable once written
           - self-describing (schema_version)
           - stored as an Artifact (bytes in Object Storage)
           - the authoritative description of the unit or recording it describes

Record     persistent SceneOps database projection / index
           - lives in PostgreSQL
           - queryable control-plane state
           - holds only searchable projections plus a reference to its Manifest
           - never the sole holder of information needed to reconstruct the unit

Artifact   physical bytes + integrity/lineage metadata
           - bytes in Object Storage (ADR-002)
           - ArtifactRecord in PostgreSQL: kind, uri, checksum, size_bytes,
             media_type, owner, producing execution
```

### 3.2 Named concepts

| Name | Kind | Meaning |
|---|---|---|
| `SceneManifest` | Manifest | Canonicalization payload for one Scene. |
| `EpisodeManifest` | Manifest | Canonicalization payload for one Episode. |
| `RobotRunManifest` | Manifest | Durable publication contract for one finalized robot recording. |
| `SceneRecord` | Record | Canonical Scene unit; a DatasetVersion member. |
| `EpisodeRecord` | Record | Canonical Episode unit; a DatasetVersion member. |
| `RobotRunRecord` | Record | Immutable, searchable projection of one finalized, verified recording. **Not** a DatasetVersion member. |
| `ArtifactRecord` | Record | SceneOps metadata/integrity record for physical bytes. |
| `DatasetVersion` | Record | Canonical membership/scope for SceneRecord and EpisodeRecord. |
| `CaptureSession` | Runtime object | In-memory recording lifecycle inside Capture Runtime. Never persisted by this ADR. |
| `CaptureSessionRecord` | DEFERRED | Possible future operational projection of capture state (§11.3). |
| `ExternalDatasetRef` | Value | Existing reference to a dataset outside SceneOps (`sceneops_core.datasets.schemas.ExternalDatasetRef`). Its `format` is an open identifier of the integration implementation, not a core enum (§20.4). |
| Integration | Component | Format-specific, DB-free runtime that maps one external format onto canonical manifests (§20.3, §20.5). |
| Source-preserving materialization | Stage | Copying the source payloads a canonical unit needs into SceneOps-owned artifacts without altering the observations (§13.9). |
| Derived transformation | Stage | Lossy or workflow-specific transformation of canonical units, such as alignment, resampling, association or fusion (§13.6). Its output is never canonical. |
| `ExternalUnitSource` | Value | Provenance block: unit came from an external dataset (§14, §27.2). |
| `RecordingSegmentSource` | Value | Provenance block: unit came from a registered recording (§14, §27.2). |
| `ProducerInfo` | Value | Provenance block: which producer semantics and configuration built the unit (§14, §15, §27.7). |
| `producer_fingerprint` | Value | Hash that identifies build semantics (§15). |
| Registrar | Component | The only writer of canonical Scene/Episode records, membership and summaries for its domain (§17.5). |

### 3.3 Not introduced

There is no universal `DataUnit`, `CanonicalUnit`, `UniversalObservation`,
universal adapter, generic lifecycle engine, generic builder or generic
registrar. Scene and Episode share platform primitives (`ArtifactRef`,
`ExternalDatasetRef`, provenance blocks, the verified recording resolver, the
DatasetVersion lock, fingerprint computation, source-clock and payload-format
primitives, the integration runtime contract) and keep explicit domain
semantics:

> Platform primitives are generic; domain semantics remain explicit.

---

## 4. Layer boundaries

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ A. External / integration layer                       (NO SceneOps DB)    │
│    nuScenes integration · LeRobot integration                            │
│    ROS2 nodes · Kafka bridge · Capture Runtime · Recording Publisher     │
├──────────────────────────────────────────────────────────────────────────┤
│ B. Durable source contracts                                              │
│    SceneManifest · EpisodeManifest · RobotRunManifest                    │
├──────────────────────────────────────────────────────────────────────────┤
│ C. Physical artifacts                                 (Object Storage)    │
│    MCAP · Parquet · video · sensor payloads · manifest JSON · reports    │
├──────────────────────────────────────────────────────────────────────────┤
│ D. Persistent SceneOps records                        (PostgreSQL)        │
│    SceneRecord · EpisodeRecord · RobotRunRecord · ArtifactRecord         │
│    DatasetVersion · Robot                                                │
├──────────────────────────────────────────────────────────────────────────┤
│ E. Derived workflows                                                     │
│    ScenarioSet · Inference · Evaluation · AlignedEpisode                 │
│    LearningDataExport · Curation · LeRobot export · dataset indexes      │
│    workflow-specific Scene views (detection, reconstruction, viz)        │
└──────────────────────────────────────────────────────────────────────────┘
```

Rules:

| Layer | May depend on | Must not |
|---|---|---|
| A | B schemas (`sceneops-core`, pure Pydantic), ArtifactStore (`sceneops-storage`), its own SDKs | import `sceneops-db`; open DB sessions; write Records; run inside Celery |
| B | nothing but `sceneops-core` schema primitives | reference DB-only identifiers that cannot be derived from the source |
| C | — | be interpreted without its ArtifactRecord's checksum when it backs a canonical unit |
| D | B, C | be written by anything other than its owning registrar (Scene/Episode/RobotRun) or its owning run/job (ArtifactRecord) |
| E | D, C via resolvers | redefine canonical membership, identity or ingestion semantics; read source-specific fields |

The layer-A rule already holds for the nuScenes integration service and
LeRobot container (verified by import-boundary tests) and for
`ros2/capture/`. The TARGET adds the Recording Publisher to layer A.

**Canonicalization stages (conceptual).** The layers above are dependency
boundaries. The stages below show the order in which a source becomes
canonical data, and where canonical data ends and derived data begins:

```text
SOURCE / ACQUISITION                 external dataset · RobotRun recording (MCAP)
        ↓
SOURCE-PRESERVING MATERIALIZATION    required source payloads → SceneOps-owned
                                     artifacts (layer C), observations unaltered
        ↓
DOMAIN CONSTRUCTION                  unit boundary selection · segmentation ·
                                     canonical identity · provenance
        ↓
CANONICAL MANIFEST                   SceneManifest · EpisodeManifest (layer B)
        ↓
CANONICAL RECORD                     SceneRecord · EpisodeRecord (layer D)
        ↓
DATASETVERSION MEMBERSHIP            registrar-owned (layer D)
═══════════════════════════════════  canonical boundary
        ↓
DERIVED TRANSFORMATIONS              layer E
  Scene   → detection-specific view · reconstruction-specific view
          → visualization view · optional future AlignedScene (§13.11)
  Episode → AlignedEpisode · LearningDataExport
```

Nothing below the canonical boundary is written back above it. Derived
outputs are reproducible from canonical manifests and their artifacts
(§13.6).

---

## 5. Manifest vs Record vs Artifact ownership

> **Amended by A4.** The rows marked "(external)" and "imported from external
> datasets" are superseded (§29.2). Recording-derived payloads are written by
> the recording builder job, which also registers their ArtifactRecords.

| Concept | Bytes written by | ArtifactRecord registered by | Record written by |
|---|---|---|---|
| Recording (MCAP) | Recording Publisher | `REGISTER_ROBOT_RUN` registrar | — |
| `RobotRunManifest` | Recording Publisher | `REGISTER_ROBOT_RUN` registrar | `RobotRunRecord` by `REGISTER_ROBOT_RUN` registrar |
| Sensor payloads imported from external datasets | integration runtime | ingest job (worker) | — |
| `SceneManifest` (external) | integration runtime | ingest job (worker) | `SceneRecord` by Scene registrar |
| `SceneManifest` (recording) | recording Scene builder job | builder job | `SceneRecord` by Scene registrar |
| `EpisodeManifest` (external) | integration runtime | ingest job (worker) | `EpisodeRecord` by Episode registrar |
| `EpisodeManifest` (recording) | recording Episode builder job | builder job | `EpisodeRecord` by Episode registrar |
| DatasetVersion summary | — | — | Scene/Episode registrar only |
| Validation / profile reports | validator / profiler job | that job | `*RunRecord` by that job |
| Dataset index / DatasetManifest | derived index job | that job | — (ArtifactRecord only) |

Why the asymmetry for RobotRun: the Recording Publisher is DB-free by
contract, so it cannot register ArtifactRecords. Its registrar therefore
registers both ArtifactRecords. Scene/Episode producers already run in the
worker, which owns DB access. They register ArtifactRecords for the bytes
they wrote, carrying their own execution lineage (`job_id`,
`pipeline_run_id`), as they do today. Registering an ArtifactRecord **never**
confers canonical membership. Only the registrar does that.

Registrars receive **manifest artifact ids**, not URIs. The registrar re-reads
the bytes and verifies them against the ArtifactRecord checksum before using
them (§17).

---

## 6. Diagrams

### 6.1 External dataset → canonical record

> **Superseded by A4 (§29.4, §29.13).** External datasets no longer map to
> canonical manifests. They become recordings outside the platform and enter
> through §6.2.

```text
ExternalDatasetRef (format, format_version, uri, external_revision, checksum?)
        │
        │  IntegrationRequest(operation=INGEST, external_ref, canonical_ref, config)
        ▼
┌───────────────────────────────┐   layer A, DB-free, isolated SDK
│ integration runtime           │
│   parse source units          │
│   import payload bytes ───────┼──▶ Object Storage (SceneOps-owned keys)
│   build SceneManifest[] /     │
│         EpisodeManifest[]     │
│   write manifests (canonical  │
│   JSON, checksum-qualified)───┼──▶ Object Storage
└───────────────┬───────────────┘
                │  IntegrationResult.produced_artifacts
                ▼
INGEST_EXTERNAL_{SCENES|EPISODES} job (worker)
   verify produced bytes; register ArtifactRecords (manifests, payloads)
   compute producer_fingerprint
                │  manifest_artifact_ids[] + fingerprint
                ▼
REGISTER_{SCENES|EPISODES} registrar (worker)
   lock DatasetVersion · re-read + verify manifests · apply identity rules (§18)
   upsert SceneRecord[] / EpisodeRecord[] · recompute summaries · commit
                ▼
DatasetVersion membership
```

### 6.2 Capture → publisher → RobotRunManifest/MCAP → registration

```text
ROS2 topics
   │  streaming_bridge_node (ADR-005 Data Gateway)
   ▼
Kafka (robot_run_id-partitioned)
   │
   ▼
┌─────────────────────────────────────┐   layer A, DB-free
│ Capture Runtime                     │
│   CaptureSession (in-memory)        │
│   DISCOVERED→RECORDING→FINALIZING   │
│            →FINALIZED | FAILED      │
│   writes .partial → atomic finalize │
└──────────────────┬──────────────────┘
                   │ finalized local MCAP
                   ▼
┌─────────────────────────────────────┐   layer A, DB-free
│ Recording Publisher                 │
│ 1 validate local MCAP, derive facts │
│ 2 upload MCAP to write-once key ────┼──▶ Object Storage: …/{run_id}/recording.mcap
│ 3 re-read, verify checksum + size   │
│ 4 build canonical manifest bytes    │
│ 5 write manifest LAST ──────────────┼──▶ Object Storage: …/{run_id}/robot_run_manifest.json
└──────────────────┬──────────────────┘   (publication marker)
                   │ manifest_uri
                   ▼
REGISTER_ROBOT_RUN(manifest_uri)            (worker job; POST /robot-runs:register)
   read + strictly parse manifest
   verify recording bytes (checksum, size, format)
   resolve Robot platform rule
   ┌──────────── one DB transaction ────────────┐
   │ ArtifactRecord(robot_run_recording)        │
   │ ArtifactRecord(robot_run_manifest)         │
   │ RobotRunRecord (immutable)                 │
   └────────────────────────────────────────────┘
```

### 6.3 RobotRun → Scene/Episode building

```text
                       RobotRunRecord(run_id)
                                │
                     resolve_recording(run_id)
              (RobotRunRecord → recording ArtifactRecord →
               materialize → checksum verify → cleanup)
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
   BUILD_RECORDING_SCENES              BUILD_RECORDING_EPISODES
   (SceneBuilder: spatiotemporal       (EpisodeBuilder: task/action
    segmentation; source-preserving     segmentation; obs/action
    payloads; channel→sensor config)    channel + clock config)
                 │                             │
     SceneManifest[] + fingerprint   EpisodeManifest[] + fingerprint
                 │                             │
                 ▼                             ▼
        REGISTER_SCENES                 REGISTER_EPISODES
   scope = (DV, scene, recording,   scope = (DV, episode, recording,
            run_id)                          run_id)
                 │                             │
                 ▼                             ▼
          SceneRecord[]                  EpisodeRecord[]
                 └──────────┬──────────────────┘
                            ▼
                     DatasetVersion
```

The same RobotRun may feed both branches, into the same or different
DatasetVersions. The two branches share no builder and no unit type.

### 6.4 Data plane vs optional future control plane

```text
DATA PLANE  (TARGET; DB-independent end to end until registration)
  Kafka ──▶ Capture Runtime ──▶ finalized MCAP ──▶ Recording Publisher
        ──▶ MCAP + RobotRunManifest (Object Storage)
        ──▶ REGISTER_ROBOT_RUN ──▶ RobotRunRecord (finalized provenance)

CONTROL PLANE  (DEFERRED; not part of this ADR's implementation)
  Capture session events / heartbeats
        ──▶ SceneOps operational service
        ──▶ CaptureSessionRecord (state, owner, lease, heartbeat, progress, failure)

  The control plane observes the data plane. The data plane never depends on it,
  never blocks on it, and never writes RobotRunRecord through it.
```

### 6.5 DatasetVersion membership

```text
Dataset
 └── DatasetVersion (dataset_id, version)
      ├── SceneRecord[]     each: source_kind, provenance projections,
      │                           producer_fingerprint, manifest_artifact_id
      ├── EpisodeRecord[]   same projection shape, Episode semantics
      └── summaries         scene/sample/frame/episode counts, observed channels
                            (cached; written only by registrars; recomputed)

NOT members / NOT owned:
  RobotRunRecord            referenced by recording-derived unit provenance
  ArtifactRecord            referenced by manifest_artifact_id
  *RunRecord                validation / profile history; readiness derived from them
  dataset index / manifest  derived ArtifactRecords
```

### 6.6 Four ingestion pipelines

> **Superseded by A4 (§29.2).** Only the two `RECORDING_*` pipelines and the
> standalone `REGISTER_ROBOT_RUN` remain. The `EXTERNAL_*` pipelines are not
> introduced.

```text
EXTERNAL_SCENE_INGESTION      ExternalDatasetRef ─▶ INGEST_EXTERNAL_SCENES ─▶ REGISTER_SCENES
EXTERNAL_EPISODE_INGESTION    ExternalDatasetRef ─▶ INGEST_EXTERNAL_EPISODES ─▶ REGISTER_EPISODES
RECORDING_SCENE_BUILDING      robot_run_id ─▶ BUILD_RECORDING_SCENES ─▶ REGISTER_SCENES
RECORDING_EPISODE_BUILDING    robot_run_id ─▶ BUILD_RECORDING_EPISODES ─▶ REGISTER_EPISODES

Optional downstream tasks in the same pipeline definition (never alter membership):
  VALIDATE_* · PROFILE_*

Prerequisite (standalone job, not a pipeline task of the above):
  REGISTER_ROBOT_RUN(manifest_uri)
```

### 6.7 Canonical convergence

> **Superseded by A4 (§29.4).** Convergence now happens one layer earlier,
> at the RobotRun: streaming and batch acquisition both produce a registered
> recording, and one builder per domain turns it into canonical manifests.
> The principle below still holds, with only the right-hand column: one
> manifest contract per domain, and downstream code never sees source-format
> identity.

Each domain has exactly one canonical manifest contract, and both source
kinds converge on it before registration:

```text
Scene
  nuScenes / future Scene-native dataset        RobotRun / MCAP
                 │                                     │
                 ▼                                     ▼
       EXTERNAL_SCENE_INGESTION             RECORDING_SCENE_BUILDING
                 │                                     │
                 ▼                                     ▼
           SceneManifest                         SceneManifest
                 └─────────────────┬───────────────────┘
                                   ▼
                    REGISTER_SCENES → SceneRecord

Episode
  LeRobot / future Episode-native dataset       RobotRun / MCAP
                 │                                     │
                 ▼                                     ▼
      EXTERNAL_EPISODE_INGESTION           RECORDING_EPISODE_BUILDING
                 │                                     │
                 ▼                                     ▼
          EpisodeManifest                       EpisodeManifest
                 └─────────────────┬───────────────────┘
                                   ▼
                  REGISTER_EPISODES → EpisodeRecord
```

The registrar and every downstream workflow operate on the canonical
contract, not on source-format identity (§20.1). Adding a Scene-native or
Episode-native format adds a box at the top of the left column (§20.3). It
does not add a column, a pipeline or a manifest type.

---

## 7. Recording publication protocol

### 7.1 Capture Runtime

Capture Runtime owns runtime recording behavior only: consuming Kafka,
routing by `robot_run_id`, writing `.partial` MCAP, finalizing atomically,
and `CaptureSession` state transitions. It must not write SceneOps records,
call SceneOps APIs to create canonical state, or depend on `sceneops-db`.

CURRENT IMPLEMENTATION: satisfied by `ros2/capture/` (`router.py`,
`finalize.py`, `mcap_writer.py`). Unchanged by this ADR.

### 7.2 Recording Publisher

TARGET CONTRACT. The Recording Publisher is a DB-free layer-A component. Its
inputs are a finalized local MCAP plus normalized publication inputs: `run_id`,
`robot_id`, optional `robot_platform`, capture source identity, source clock,
and the target storage root. It performs these steps in this order:

```text
P1  validate local recording
      - path is not a .partial capture path
      - opens as declared format (MCAP); ≥ 1 message
      - derive facts: started_at, ended_at, channels (topic, encodings, schema, count)
      - compute checksum ("sha256:<hex>") and size_bytes

P2  derive deterministic recording key from run_id (write-once)

P3  publish recording
      key absent            → upload; re-read; verify checksum + size
      key present, same     → reuse (idempotent retry)
      key present, differs  → FAIL (conflict); never overwrite

P4  build canonical RobotRunManifest bytes (§8.3)

P5  publish manifest LAST at the deterministic manifest key
      key absent            → write; re-read; verify byte equality
      key present, same     → no-op (idempotent retry)
      key present, differs  → FAIL (conflict); never overwrite

P6  (optional) submit REGISTER_ROBOT_RUN(manifest_uri)
```

**Publication-marker invariant.** If a valid `RobotRunManifest` exists at a
manifest key, the referenced recording was already durably published and
verified by the publisher. A crash anywhere before P5 leaves no manifest, so
no recording is ever considered published. Orphaned recording bytes without
a manifest are harmless and are reused by a retry (P3).

**Registration submission is decoupled.** P6 is a convenience. A failure to
submit does not invalidate the publication. Registration is idempotent and
can be triggered later by an operator or automation from the manifest URI.
Automatic discovery of published-but-unregistered manifests is DEFERRED
(§26).

**Write-once enforcement.** On a single object store, "check, then write" is
not atomic. The v1 contract relies on two things:

1. a single publisher per `run_id`, which capture routing by `robot_run_id`
   already provides;
2. verification downstream. Registration re-verifies recording checksum and
   size against the manifest, and every consumer re-verifies on
   materialization.

As a result, a write-once violation caused by a racing publisher with
different bytes is **detected and fails loudly**. It is never silently
accepted. Conditional writes (`If-None-Match: *`) are the preferred hardening
when the ArtifactStore backend supports them. That hardening is an
implementation choice and does not change this contract.

**Registration never moves or rewrites recording bytes.**

CURRENT IMPLEMENTATION: there is no separate publisher.
`register_robot_run_capture` (worker) does P1–P3 and then DB registration in
one call, and writes no manifest. `sceneops-worker robots register-capture`
is its CLI.

### 7.3 Placement

The publisher must not import `sceneops-db` and must not run inside the
worker's Celery process. It depends only on `sceneops-core` (the
`RobotRunManifest` schema and canonical serializer) and `sceneops-storage`.
Its exact package location is decided in implementation step 1 (§24).
Candidates are `ros2/capture/` or a small DB-free package. Whichever is
chosen gets an import-boundary test equivalent to the existing nuScenes /
LeRobot ones.

---

## 8. RobotRunManifest v1

### 8.1 Content rule

A `RobotRunManifest` contains **facts about the source recording and its
capture**. It contains nothing that a downstream SceneOps process decides.

### 8.2 Schema (TARGET CONTRACT)

```text
RobotRunManifest v1
  schema_version     "sceneops.robot_run_manifest/v1"          required
  run_id             str                                        required
  robot_id           str                                        required
  robot_platform     str | null                                 optional (source assertion, §9)
  started_at         timestamp (§8.3)                           required
  ended_at           timestamp (§8.3)                           required, ≥ started_at
  recording          required
    format           "mcap"                                     required (closed set in v1)
    uri              str (ArtifactStore URI)                    required
    checksum         "sha256:<64 lowercase hex>"                required
    size_bytes       int ≥ 1                                    required
  capture            required
    source           required
      kind           "kafka" | "ros2_bag" | "file"              required
      topics         [str] (sorted, unique)                     required iff kind = "kafka"
    source_clock     str  (e.g. "mcap_log_time")                required
  channels           [ChannelFact] sorted by topic              required, ≥ 1
    topic            str
    message_encoding str   (e.g. "cdr")
    schema_name      str   (e.g. "nav_msgs/msg/Odometry")
    schema_encoding  str   (e.g. "ros2msg")
    message_count    int ≥ 1
```

`started_at` / `ended_at` are the minimum and maximum message timestamps in
the recording under `capture.source_clock`. They are derived from the
recording bytes, not taken from wall-clock time when publication happened.

Excluded. Must not appear and is rejected as an unknown field:

```text
dataset_id / dataset_version / any DatasetVersion reference
scene ids / episode ids / mission-derived unit ids
validation / profiling / learning / evaluation state
downstream processing state ("ingested", "built", …)
generated_at / published_at / any publication timestamp
Kafka partitions / offsets / consumer group  (operational; see §11.3)
free-form metadata maps
topic → modality / sensor mapping  (belongs to build configuration; §13.8)
```

Example (shown pretty-printed for readability; the canonical form is compact, §8.3):

```json
{
  "capture": {
    "source": {"kind": "kafka", "topics": ["robot.telemetry.v1"]},
    "source_clock": "mcap_log_time"
  },
  "channels": [
    {"message_count": 1180, "message_encoding": "cdr",
     "schema_encoding": "ros2msg", "schema_name": "nav_msgs/msg/Odometry",
     "topic": "/odom"}
  ],
  "ended_at": "2026-10-02T05:12:44.123456Z",
  "recording": {
    "checksum": "sha256:9f2c…",
    "format": "mcap",
    "size_bytes": 1048576,
    "uri": "s3://sceneops/robot-runs/run-001/recording.mcap"
  },
  "robot_id": "robot-001",
  "robot_platform": "nuscenes-can-replay",
  "run_id": "run-001",
  "schema_version": "sceneops.robot_run_manifest/v1",
  "started_at": "2026-10-02T05:10:02.000000Z"
}
```

### 8.3 Canonical serialization and determinism

**Determinism contract.** Identical normalized publication inputs produce
byte-identical `RobotRunManifest` bytes.

Normalized publication inputs are: the recording bytes; `run_id`;
`robot_id`; `robot_platform`; the capture source identity; the source clock;
and the storage root from which the recording URI is derived. The contract
**does not** claim that the same recording content published under a
different URI, storage root, backend or other publication input yields the
same manifest. It does not.

Canonical v1 serialization:

```text
encoding          UTF-8, no BOM
format            JSON, compact separators (",", ":"), no insignificant whitespace,
                  no trailing newline
key order         lexicographic by Unicode code point, at every nesting level
arrays            order defined by the schema (channels by topic; topics sorted)
strings           non-ASCII emitted as UTF-8, not \u-escaped
numbers           integers only in v1 (no floating-point fields)
null              optional fields absent → emitted as null (robot_platform)
timestamps        RFC 3339, UTC, "Z" suffix, exactly 6 fractional digits
                  (microsecond precision; finer source precision truncated toward
                  negative infinity)
excluded          generated-at timestamps, random ids, host names, process ids,
                  free-form metadata
```

Registration enforces canonical form. It parses the manifest strictly
(unknown fields are rejected), re-serializes the parsed value with the
canonical serializer, and **requires the result to be byte-equal to the
stored bytes**. A non-canonical manifest is rejected, even when it is
semantically equal to a canonical one.

The canonical serializer lives in `sceneops-core` and is shared by the
publisher and the registrar. Scene and Episode manifests use the same
serializer. For their floating-point fields it adds this rule: floats are
emitted in shortest round-trip decimal representation, and NaN/Infinity are
rejected.

**Retry convergence.** A publisher retry with identical normalized inputs
produces identical manifest bytes, so P5 hits the "present, same" branch.

### 8.4 Versioning

`schema_version` is a closed value per major version. A v2 is introduced
only when a v1 field's meaning changes or a new required fact is needed. The
registrar dispatches on `schema_version` and rejects unknown versions.

---

## 9. Robot identity conflict rule

```text
RobotRecord.platform              authoritative robot identity property
RobotRunManifest.robot_platform   optional source assertion
```

Registration behavior, evaluated inside the registration transaction:

| RobotRecord | manifest `robot_platform` | Result |
|---|---|---|
| absent | given | create RobotRecord with that platform |
| absent | null | create RobotRecord with `platform = null` |
| exists, `platform` null | given | fill once |
| exists, `platform` set | equal | accept |
| exists, `platform` set | different | **fail registration** (no state change) |
| exists, any | null | accept; do not touch RobotRecord |

Last-write-wins is never used. Changing a robot's platform is an explicit
Robot operation outside ingestion.

CURRENT IMPLEMENTATION: `register_robot_run_capture` calls
`upsert_robot(RobotRecord(robot_id, platform=platform))` with a hard-coded
default `platform="nuscenes-can-replay"`. That is last-write-wins and is
removed.

---

## 10. RobotRunRecord contract

### 10.1 Meaning

> The existence of a `RobotRunRecord` means the recording is finalized,
> durably published, and verified.

There is no other state. A RobotRun that is recording, failed, or not yet
verified does not have a RobotRunRecord.

```text
CaptureSession    runtime recording lifecycle   (layer A, in-memory)
RobotRunRecord    finalized recording provenance (layer D, immutable)
```

### 10.2 Fields (TARGET CONTRACT)

```text
run_id                   PK                     (manifest.run_id)
robot_id                 FK robots              (manifest.robot_id)
started_at               not null               (manifest.started_at)
ended_at                 not null               (manifest.ended_at)
recording_format         not null               (manifest.recording.format)
source_clock             not null               (manifest.capture.source_clock)
recording_artifact_id    not null               → ArtifactRecord(kind=robot_run_recording)
manifest_artifact_id     not null               → ArtifactRecord(kind=robot_run_manifest)
manifest_checksum        not null               sha256 of canonical manifest bytes
registered_at            not null               DB insert time (not a manifest field)
```

Recording URI, checksum and size live **only** on the recording
`ArtifactRecord`. The full source facts (channels, capture source) live
**only** in the manifest. The record is an index over them.

### 10.3 Removed from RobotRun

```text
status (and RobotRunStatus: recording / completed / ingested / failed)
dataset_id, dataset_version
raw_log_id
mcap_uri, rosbag_uri
metadata (free-form JSONB)
updated_at  (record is immutable; there is nothing to update)
```

### 10.4 Immutability

A RobotRunRecord is never updated after insert. There is no replacement
semantics (§18.4). Missions, robot states, and other telemetry-derived
projections keyed by `robot_run_id` are separate derived records produced
from the resolved recording (§12.4). They do not mutate the RobotRunRecord.

---

## 11. CaptureSession and the future realtime control plane

### 11.1 Separation

Realtime recording lifecycle **must not** be put back into RobotRunRecord. A
RobotRunRecord is finalized provenance. A mutable status column on it would
mix operational state with canonical identity and would reintroduce the
"RobotRun without verified recording" state this ADR removes.

### 11.2 CURRENT / TARGET

`CaptureSession` is an in-memory runtime object in Capture Runtime with
states `DISCOVERED`, `RECORDING`, `FINALIZING`, `FINALIZED`, `FAILED`. It is
not persisted. A process restart loses in-flight session state. This is a
known limitation, already documented for continuous multi-run capture, and
this ADR does not change it.

### 11.3 DEFERRED: `CaptureSessionRecord`

If realtime operator visibility, crash recovery, leases, heartbeats,
progress tracking or orphan detection become requirements, SceneOps may add
a separate **operational control-plane** model:

```text
CaptureSession          runtime object (exists)
CaptureSessionRecord    optional future durable operational projection
```

Possible concerns of that model:

```text
state · worker ownership · heartbeat · lease
Kafka partition / offset range · message count · byte count
failure reason · recovery checkpoint
```

Constraints that any future control plane must honor:

1. The capture **data path stays DB-independent**. Capture publishes events
   or heartbeats; a SceneOps operational service persists them. A control
   plane outage must not block capture, finalization or publication.
2. `CaptureSessionRecord` is not canonical provenance. It never substitutes
   for, mutates, or gates `RobotRunRecord`. The only route to a RobotRunRecord
   is `REGISTER_ROBOT_RUN` over a published manifest.
3. A `CaptureSessionRecord` may *reference* the `run_id` and, once
   registered, the RobotRunRecord. The reverse reference is not allowed.

Durable capture recovery (process restart, Kafka rebalance) is explicitly
outside this ADR's implementation scope.

*See [ADR-008](./008-acquisition-lifecycle-reliability.md): the operational
lifecycle after capture (receipts, publication and registration recovery,
reconciliation, artifact classification, derived status) is resolved there
without a `CaptureSessionRecord`, and records why this section's DEFERRED status
and constraints stand.*

---

## 12. Registration protocol

### 12.1 `REGISTER_ROBOT_RUN(manifest_uri)`

A normal SceneOps worker job (`JobType.REGISTER_ROBOT_RUN`). It is
standalone and not a task of any ingestion pipeline. Submission paths:

```text
POST /robot-runs:register   { "manifest_uri": "…" }   → Job (202 + job_id)
sceneops-worker robots register --manifest-uri …      → same job handler
```

Steps:

```text
R1  read manifest bytes from manifest_uri
R2  strict parse (unknown fields rejected; schema_version known)
R3  canonical-form check: canonical(parse(bytes)) == bytes
R4  manifest_checksum = sha256(bytes)
R5  existing RobotRunRecord(run_id)?
      yes, manifest_checksum equal   → idempotent no-op; return existing
      yes, manifest_checksum differs → HARD CONFLICT; fail; no state change
R6  verify recording at manifest.recording.uri:
      exists; size == size_bytes; sha256 == checksum
      opens as declared format; channel set and message counts match manifest.channels
R7  evaluate Robot platform rule (§9)
R8  one DB transaction:
      Robot create / fill-once (if applicable)
      ArtifactRecord(kind=robot_run_recording, id=robot_run_recording_artifact_id(run_id),
                     uri, checksum, size_bytes, owner=robot_run/run_id)
      ArtifactRecord(kind=robot_run_manifest,  id=robot_run_manifest_artifact_id(run_id),
                     uri=manifest_uri, checksum=manifest_checksum, size_bytes,
                     owner=robot_run/run_id)
      RobotRunRecord(…)
    commit
R9  on unique-constraint race (concurrent registration of the same run_id):
      rollback; reload; resolve exactly as R5
```

Failure semantics:

| Failure point | State after | Retry behavior |
|---|---|---|
| R1–R7 | no DB change | retry re-evaluates from R1 |
| R8 before commit | rolled back; no DB change | retry converges |
| R8 commit raced | one winner | loser resolves via R9 → no-op or conflict |
| Partial ArtifactRecord without RobotRunRecord | impossible by single transaction; if observed, reported as inconsistent canonical state and never repaired silently | — |

Registration never uploads, copies, moves or rewrites recording or manifest
bytes.

### 12.2 Removed creation paths

Every path that can produce a RobotRun without a verified manifest and
recording artifact is removed: `POST /robot-runs` (bare create),
`CreateRobotRunRequest`, `sceneops-worker robots register-run`, and the
DB-coupled `register-capture` (its P1–P3 responsibilities move to the
publisher; its DB responsibilities move to `REGISTER_ROBOT_RUN`).

### 12.3 Scene / Episode registration

Covered in §17. Shared properties with RobotRun registration: the registrar
receives references, re-reads and verifies bytes, writes in one transaction,
converges on identical retries, and fails loudly on conflict.

### 12.4 Verified recording resolver

All consumers of a registered recording use one resolver:

```text
resolve_recording(robot_run_id)
  → RobotRunRecord(robot_run_id)                       (missing → fail)
  → ArtifactRecord(recording_artifact_id)              (missing → inconsistent state; fail)
  → materialize bytes to an execution-scoped local path via ArtifactStore
  → verify sha256 against ArtifactRecord.checksum      (mismatch → fail)
  → yield VerifiedRecording(path, robot_run_id, recording_artifact_id,
                            checksum, format, source_clock)
  → delete local copy on exit (success and exception)
```

Consumers (TARGET):

```text
BUILD_RECORDING_SCENES
BUILD_RECORDING_EPISODES
robot-state / mission / telemetry-derived processing (INGEST_ROBOT_STATES, analytics snapshot)
```

No canonical path accepts a bare `mcap_uri`, a local path, or a
DatasetVersion raw source path as an equivalent recording source. The
local-path branch (`is_local_uri` / `verify_local_recording_checksum`) is
removed. A local filesystem backend is reached through `LocalArtifactStore`
like any other backend.

CURRENT IMPLEMENTATION: `materialize_recording()` in
`apps/worker/sceneops_worker/robots/materialization.py` already performs
materialize + verify + cleanup for `build_episodes`. It reads the whole
object into memory (`read_bytes`). Streaming materialization for large
recordings is a known limitation and DEFERRED. It does not change this
contract.

---

## 13. Scene and Episode canonicalization

### 13.1 Domain semantics stay separate

```text
Scene     spatiotemporal environmental observation unit
          sensor observations × calibrations × ego poses × annotations
          segmentation = spatial/temporal windows; observations keep source
          timing (§13.8); sample grouping/association is derived (§13.6)
          unless the source itself defines it

Episode   task / interaction / action-state trajectory unit
          observation frames × action frames × control frequency × outcome
          segmentation = task / mission / interaction boundaries
```

> **Amended by A6 (§31.2).** Canonical Episode content is asynchronous
> observation, state, action and event streams, each in its own clock.
> "Frames", control frequency and outcome are derived (`AlignedEpisode`)
> or later-imported labels, not canonical Episode facts.

The same RobotRun may yield `SceneRecord[]` and `EpisodeRecord[]` through
different builders with different segmentation semantics. `SceneBuilder` and
`EpisodeBuilder` are not merged, and neither is a parameterization of a
universal builder.

### 13.2 Shared infrastructure (allowed)

```text
ArtifactRef · ExternalDatasetRef
ExternalUnitSource · RecordingSegmentSource · ProducerInfo   (§14)
producer fingerprint computation                             (§15)
canonical JSON serializer                                    (§8.3)
verified recording resolver                                  (§12.4)
integration runtime contract (IntegrationRequest / IntegrationResult)
DatasetVersion lock + summary recompute primitive
```

These are platform primitives. Each domain composes them into its own
lineage type, record type, registrar and builder.

### 13.3 Canonical record shape (TARGET; projection only)

Both records keep only searchable projections plus a pointer to the
manifest revision that is authoritative for them:

```text
SceneRecord / EpisodeRecord (common projection columns)
  <unit>_id                 deterministic (§18.1)
  dataset_id, dataset_version
  source_kind               external | recording
  robot_run_id              null unless source_kind = recording
  external_format           null unless source_kind = external
  source_unit_key           external: source unit key; recording: builder unit key
  producer_fingerprint
  manifest_artifact_id      → the current manifest revision
  started_at, ended_at
  registered_at, updated_at
  (no status column — record existence = registered, §13.4)

  + domain-specific searchable projections, e.g.
    Scene:   sample_count, frame_count, annotation_count, channels, has_ground_truth
    Episode: task, outcome, observation_channels, action_channels,
             control_frequency_hz, frame_count
```

Removed from records: `raw_log_id`, `segment_id`, `scene_manifest_uri` /
`episode_manifest_uri` (replaced by `manifest_artifact_id`), `lineage` /
`generation` JSONB copies (full provenance lives in the manifest),
`status` (§13.4), `world_state_manifest_uri`, `artifact_root_uri`, `mission_id` /
`robot_id` on EpisodeRecord as identity fields. `origin_type` and
`generation_method` are replaced by `source_kind` + producer identity.
Simulated, generated or reconstructed Scenes are DEFERRED (§26).

### 13.4 Status and readiness

TARGET CONTRACT:

```text
SceneRecord.status     → REMOVE
EpisodeRecord.status   → REMOVE
SceneStatus            → REMOVE (enum)
EpisodeStatus          → REMOVE (enum)
```

**Record existence itself means the canonical unit is registered.** A
registrar creates a record only when it registers the unit, and there is no
earlier canonical state to represent, because builders and ingestors produce
manifests, not records. A status column would carry no information, so it
is not kept.

Validation, profiling and readiness are represented by their run records
(`SceneRunRecord` / `EpisodeRunRecord`) and by readiness views derived from
them. They are never represented by mutable canonical-record status.
Readiness is derived only from run records that assessed the record's
**current** manifest revision (run records keep the manifest artifact id or
checksum they assessed). A run record for an older revision does not count
toward the current revision's readiness.

A lifecycle or quality status column must not be reintroduced on
`SceneRecord` or `EpisodeRecord`. If an operational state is ever needed for
a unit, for example soft deletion or quarantine, it requires its own
explicit decision and its own model. It is not added back as `status`.

Same principle as RobotRunRecord, which has no status (§10).

CURRENT IMPLEMENTATION: both records carry `status`. `SceneStatus` is
`created` / `built` / `validated` / `profiled` / `failed`, and
validate/profile jobs mutate it. `EpisodeStatus` is `created` /
`registered`, and readiness is already derived from run records.

### 13.5 Source-faithful canonicalization

> **Canonical units are source-faithful within their selected domain
> boundary.**

Canonicalization **may**:

- select a domain unit boundary: which source span, source unit and set of
  source channels becomes one Scene or Episode;
- segment a larger recording or external dataset into units;
- normalize representation into SceneOps domain contracts (schema,
  canonical serialization, declared conventions);
- materialize source payloads into SceneOps-owned storage (§13.9);
- assign canonical identity (§18.1);
- normalize representation details that are **explicitly equivalent**,
  meaning that a conversion loses no information and is declared, for
  example quaternion component order under a declared convention, or units
  of measure under a declared unit.

Canonicalization **must not** discard or alter source observations within
the selected boundary merely to satisfy a specific downstream workflow.

What the boundary covers. A boundary is defined over time or source units
**and** over an explicitly configured set of source channels. Excluding a
whole channel is a boundary decision. It is recorded in
`ProducerInfo.build_config` and is therefore part of the
`producer_fingerprint`. Dropping *some* observations of an included channel
inside the selected window is not a boundary decision; it is lossy
sampling, which is derived (§13.6).

What "source-faithful" means. The interpretation-relevant information about
each observation inside the boundary remains reconstructable from the
canonical manifest and its SceneOps-owned artifacts:

```text
the observation payload           (bytes, declared format / encoding)
its source timing                  (source timestamp under a declared source clock)
its source identity                (source channel / topic / sensor as named by the source)
what is needed to interpret it     (calibration, coordinate-frame semantics,
                                    poses / transforms, annotations where available)
where it came from                 (source and producer provenance, §14)
```

What it does **not** mean. It is not byte-for-byte archival of the source
container or the source SDK's view of it. SDK internals, caches, indexes,
temporary parsing state, and source information outside the selected
boundary are not preserved. The goal is preservation of
interpretation-relevant source information.

### 13.6 Canonicalization vs derived transformation

```text
SOURCE
  ↓
source-preserving materialization
  ↓
domain construction / segmentation
  ↓
CANONICAL MANIFEST
  ↓
CANONICAL RECORD
──────────────────────────── canonical boundary
  ↓
DERIVED TRANSFORMATION
```

Canonicalization defines:

```text
Scene boundary · Episode boundary
domain identity
source references
payload ownership
source provenance
```

Derived workflows own lossy or workflow-specific transformations:

```text
temporal alignment                 resampling / downsampling
interpolation                      nearest-frame association
sensor synchronization             sensor fusion
coordinate conversion for a specific model or workflow
model-specific preprocessing       feature extraction
workflow-specific filtering
```

> **Rule.** Canonicalization may define domain boundaries and normalize
> representation. Lossy sampling, temporal alignment, interpolation, sensor
> association, fusion and model-specific transformation belong to derived
> workflows, unless they are intrinsic to the domain definition itself.

**Domain-defining vs downstream-convenience transformation.** The rule is not
absolute, because building a domain unit sometimes requires interpretation.
A transformation is *domain-defining* when the canonical unit cannot be
stated without it:

```text
domain-defining (canonical)                        downstream convenience (derived)
────────────────────────────────────────────────   ───────────────────────────────────────────
segmenting a recording into Scene time windows     anchor-channel sample grouping
detecting task / mission boundaries for Episodes   nearest / previous / next frame association
choosing the configured channel set (§13.5)        every-nth / max-N downsampling
carrying a grouping the source itself defines      dropping samples that miss a channel
  (e.g. nuScenes keyframe `sample`s, LeRobot       nearest / interpolated ego-pose per frame
   frame index), as source semantics               resampling to a control or training rate
                                                   camera–lidar synchronization
```

A practical test: if two reasonable downstream workflows could want
different answers, the transformation is derived.

A domain-defining transformation still has to meet two constraints. It is
recorded in `ProducerInfo.build_config`, so it is part of the fingerprint.
It also **interprets** the observations it groups without replacing them. A
canonical manifest may carry non-lossy indexes over preserved observations,
such as a source-defined keyframe grouping or a segment boundary. The
observations those indexes reference stay independently represented, with
their own timing.

### 13.7 Scene: canonical meaning

A Scene is not the entire raw recording. It is:

> a canonical spatiotemporal environmental observation unit whose selected
> source observations remain reconstructable and interpretable without
> depending on a specific downstream workflow.

For a selected Scene boundary, the canonical Scene representation must be
able to preserve:

```text
scene boundary
observations
  source timestamp · source clock
  source channel identity
  canonical modality · sensor identity
  payload artifact reference · payload format / encoding
calibration
coordinate-frame semantics
ego pose / transforms              where available
annotations / ground truth         where available
source provenance
producer provenance
```

These are **semantic requirements**, not a schema. The concrete
`SceneManifest` schema is defined by the Canonical Scene representation
refactor (§24, step 5) and must satisfy them.

### 13.8 Source timing and source channel identity

**Source timing is preserved.** Each canonical observation keeps its own
source timestamp under a declared source clock. Canonicalization does not
replace observation times with an aligned timeline. For example:

```text
camera  timestamp = 10.037
lidar   timestamp = 10.012
```

both remain independently representable in canonical Scene data. They are
not canonicalized to

```text
camera  = 10.000
lidar   = 10.000
```

just because a downstream consumer prefers synchronized data. Temporal
synchronization is a derived transformation, unless synchronization itself
defines the source domain unit. A source-defined grouping, such as a
nuScenes `sample` timestamp, may be carried as a non-lossy index alongside
the per-observation source timestamps (§13.6). It does not replace them.

**Source channel identity is distinct from canonical semantics.**

```text
source identity         ROS:      /camera/front/image
                        nuScenes: CAM_FRONT
canonical semantics     modality = camera   (both)
                        sensor identity / role, where useful
```

Canonicalization preserves the source channel identity verbatim and adds
source-independent semantic fields where they are useful. No source has to
masquerade as the vocabulary of another external format. In particular,
ROS topics are not renamed to nuScenes channel names to satisfy downstream
code. Mapping a source channel to modality or sensor identity is
integration or build configuration (§17.3). Derived workflows select
observations by canonical semantics or by an explicitly configured source
channel, never by a hard-coded vocabulary of one format.

### 13.9 Source payload ownership (v1)

> **Amended by A4.** The external-ingestion half of this section is
> superseded: no canonical external ingestion exists (§29.2). The
> recording-source rule and §27.5 stand: canonical payloads are
> SceneOps-owned artifacts extracted from the registered recording.

For canonical external ingestion, SceneOps v1 materializes the source
payloads a canonical unit requires into SceneOps-controlled artifact storage.
Canonical data must not depend on the continued presence of an external
dataset directory:

```text
External dataset
      ↓
integration (format-specific, DB-free)
      ↓
SceneOps-owned source artifacts  (deterministic write-once keys, checksums, declared format)
      +
canonical manifest               (references only those artifacts)
```

After canonicalization there is no dependency of the form

```text
SceneRecord / SceneManifest → external dataset root path
```

`ExternalDatasetRef.uri` is a locator for the integration only; it is not
part of canonical provenance (§14.3, §28).

For recording sources, the recording is already a SceneOps-owned,
checksum-verified artifact (§7, §12.4). Canonical payload references for
recording-derived units must also resolve only to SceneOps-owned verified
artifacts. A2 decides how: selected observation payloads are extracted into
SceneOps-owned canonical payload artifacts, and the registered recording is
retained as source provenance, not addressed into (§27.5).

**Trade-off.** Materialization duplicates the ingested portion of an
external dataset into SceneOps storage, and ingestion time includes copying
and hashing those bytes. In return, canonical data survives the external
directory being moved, mutated or deleted. Every payload is
checksum-verifiable, and consumers use one ArtifactStore path for all
sources.

**DEFERRED: reference-only / zero-copy external source mode.** A future mode
may reference external payloads in place for very large datasets. If it is
added, it is an explicit, provenance-recorded mode, and it must keep the
same canonical semantics: declared formats, verifiable checksums, the same
manifest meaning, and no layout or SDK dependency downstream. It may change
only who owns the payload bytes. It must not redefine what a canonical unit
is.

### 13.10 Episode follows the same principle

An Episode is a canonical task / interaction / action-state trajectory unit.
Canonical Episode data preserves source observations, actions, states, task
semantics, timing and provenance well enough for later derived
transformations to be computed from it:

```text
Episode  ──ALIGN_EPISODE──▶  AlignedEpisode   (derived)
```

`AlignedEpisode` is derived. The canonical Episode is never rewritten into a
training timeline. Action/observation channel mapping and the source clock
are build configuration and provenance (§17.4, §20.2). They are not
resampling.

The concrete `EpisodeManifest` shape is decided in Canonical Episode
generalization (§24, step 8). It is not redesigned here.

### 13.11 Scene alignment is optional and derived

Scene workflows may need temporal synchronization, sensor association, pose
interpolation, multi-camera synchronization or sensor fusion. In v1 these
remain **workflow-specific derived transformations**. `AlignedScene` is not
a domain type and is not part of the current canonical model.

If several workflows later converge on the same reusable alignment contract,
SceneOps may introduce

```text
Scene  ──ALIGN_SCENE──▶  AlignedScene   (derived artifact)
```

on the same terms as `AlignedEpisode`: derived, pinned to the manifest
revision it consumed (§18.5), never canonical. This is a future option
(§26).

### 13.12 CURRENT IMPLEMENTATION DEBT: Scene source fidelity

The items below describe the repository at HEAD 1663922. They are **CURRENT
IMPLEMENTATION DEBT**, not target behavior. The Canonical Scene
representation refactor and the steps after it (§24) must remove them. Items
that §20.2 already lists are cross-referenced rather than repeated.

1. **Canonical Scene payloads depend on an external source root.** The
   nuScenes integration writes `SceneSensorFrameManifest.uri =
   sample_data["filename"]`, a path relative to the dataroot
   (`sceneops_integrations/nuscenes/scene_ingest.py`). Detection resolves it
   against `DatasetVersion.scene.raw_source_root_uri` through
   `resolve_raw_uri`, which supports `file://` only
   (`inference/detection/sample_selector.py`, `grounding_dino.py`, `uris.py`).
   See §20.2, row 1.
2. **Raw Scene building does not materialize sensor payloads.**
   `SceneAssembler._build_scene_frame` copies `raw_frame.uri` from the raw-log
   frame index. Under the default `NUSCENES_RAW_LOG_MOCK` source, that is again
   a dataroot-relative path (`sceneops_integrations/nuscenes/raw_log.py`). No
   path moves sensor payloads into SceneOps-owned storage, and no registered
   recording carries camera or lidar payloads (§17.3).
3. **Source channel semantics are partially normalized into nuScenes
   vocabulary.** `SceneSensorFrameManifest` has one `channel` field and no
   separate source channel identity or sensor identity (`modality` exists).
   Core defaults use nuScenes names: `constants/sensors.py`
   (`CAMERA_CHANNELS`, `LIDAR_CHANNELS`, `DEFAULT_TARGET_CHANNELS`) and
   `SampleGroupingConfig.anchor_channel = "LIDAR_TOP"`. See §20.2, row 3.
4. **Detection has source-format and channel assumptions.**
   `camera_channel = "CAM_FRONT"` is the default in
   `jobs/schemas/params/detection.py`, `inference/schemas/detection.py` and
   `grounding_dino.py` (`_DEFAULT_CAMERA_CHANNEL`). `sample_selector.py` looks
   up `"LIDAR_TOP"`. `frustum_lifting.py` assumes the nuScenes `.pcd.bin`
   lidar layout. No Scene reconstruction workflow exists at this HEAD, so
   there is no reconstruction debt to record.
   (`sceneops_analytics/learning_dataset/reconstruct.py` is Episode learning
   data, not Scene reconstruction.)
5. **Scene provenance is incomplete.** `SceneLineage` has `raw_log_id`,
   `segment_id`, `source_dataset_*`, `source_scene_id` and free-form
   `metadata`. It has no `ExternalDatasetRef` (format, format version,
   revision), no source clock, and no producer identity, configuration or
   fingerprint. Frame-level source identity exists only in free-form
   `metadata` (`source_sample_data_id`, `raw_sequence_id`). See §1.1.
6. **Sampling and association happen too early, inside canonical Scene
   construction.**
   - `SceneManifest` represents observations only as
     `samples[].sensor_frames`. An observation that is not associated with a
     sample cannot be represented at all.
   - The raw Scene builder (`scenes/building/association.py`, `assembler.py`,
     `resolvers.py`) does several things inside canonical construction. It
     groups frames by anchor channel and associates them by nearest /
     previous / next within a tolerance. It downsamples with
     `every_nth_anchor` and `max_samples`. It drops samples through
     `drop_empty_samples` and `drop_samples_missing_required_channels`. It
     also resolves ego pose per frame by nearest match. Frames that are not
     associated are absent from the canonical manifest.
   - The nuScenes integration represents only the keyframe `sample_data`
     reachable from `sample["data"]`. Non-keyframe sweeps inside the scene's
     window are not represented. The keyframe grouping itself is
     source-defined and is legitimately preserved (§13.6). The omission of
     the sweeps is the debt. A2 rules out a keyframes-only boundary for
     canonical nuScenes Scenes (§27.4).

Already compliant: per-frame `timestamp_us` comes from the source frame, not
from the sample timestamp, in both the nuScenes integration and the raw
builder. Episode building does not resample: `control_frequency_hz` is an
observed rate, and alignment is already the derived `ALIGN_EPISODE`.

---

## 14. Provenance model

### 14.1 Building blocks (`sceneops-core`, TARGET)

> **Amended by A4 (§29.9).** `ExternalUnitSource` and its external source
> revision are removed from canonical provenance. `RecordingSegmentSource`
> and `ProducerInfo` stand unchanged.

Frozen by A2 (§27.2, §27.7) and implemented in `sceneops_core.provenance`:

```text
ExternalUnitSource                                         (refined by A3, §28)
  source_kind        = "external"
  revision           ExternalSourceRevision (format, format_version,
                                             external_revision?, checksum?)
  source_unit_key    str  (e.g. nuScenes scene token, LeRobot episode_index)
                     no location or display name: ExternalDatasetRef.uri and
                     external_name are dropped when the integration
                     canonicalizes the unit

RecordingSegmentSource
  source_kind        = "recording"
  robot_run_id       str
  recording_artifact_id   str  (= robot_run_recording_artifact_id(robot_run_id))
  recording_checksum      "sha256:…"
  source_clock       str  (copied from RobotRunRecord / manifest)
  start_timestamp_ns, end_timestamp_ns
                     integer ns in source_clock; half-open [start, end)
  unit_key           builder-defined stable key within the recording
                     (e.g. segment index, mission boundary key)

ProducerInfo
  producer_id        stable producer identity (e.g. "sceneops.recording_scene_builder")
  semantics_version  int ≥ 1, bumped per §15.3
  build_config       normalized build configuration (canonical JSON object)
  producer_fingerprint   §15
```

### 14.2 Composition (domain types stay separate)

```text
SceneLineage
  source:   ExternalUnitSource | RecordingSegmentSource   (discriminated by source_kind)
  producer: ProducerInfo
  + Scene-specific lineage (e.g. parent scene ids for derived scenes — DEFERRED)

EpisodeLineage
  source:   ExternalUnitSource | RecordingSegmentSource
  producer: ProducerInfo
  + Episode-specific lineage (e.g. segmentation boundary semantics)
```

### 14.3 Where provenance lives

- **Full provenance** is stored inside the immutable, registered Scene or
  Episode manifest.
- **Records** keep only the searchable projections listed in §13.3
  (`source_kind`, `robot_run_id`, `external_format`, `source_unit_key`,
  `producer_fingerprint`, `manifest_artifact_id`).
- `ExternalDatasetRef.uri` is a **locator**, not provenance (A3, §28). It
  lets an integration find the source before canonicalization; it is never
  part of canonical identity, never written into a canonical manifest, and
  downstream workflows never use it (§20).

### 14.4 Current revision

The current manifest revision of a unit is **exactly** the artifact named by
the record's `manifest_artifact_id`. It is never resolved as "latest
artifact by `created_at`", "latest object under a prefix", or "the manifest
at a well-known key".

---

## 15. Producer fingerprint

### 15.1 Purpose

`producer_fingerprint` identifies **the semantics of a canonical build**. Two
builds are equivalent if and only if their fingerprints are equal.

### 15.2 Definition

```text
producer_fingerprint = "sha256:" + hex(sha256(canonical_json({
    "fingerprint_schema": "sceneops.producer_fingerprint/v1",
    "producer_id":        …,
    "semantics_version":  …,
    "build_config":       normalized build configuration,
    "source":             source revision
})))

source revision
  recording:  { "source_kind": "recording", "robot_run_id", "recording_checksum" }
  external:   { "source_kind": "external", "format", "format_version",
                "external_revision" | null, "checksum" | null }
```

The exact contract, including why the source revision excludes the unit key
and window, is §27.7.

It is computed from **inputs**, so it is known before building. That allows
the "same fingerprint → reuse" decision (§18.3) to short-circuit the build.

Normalization of `build_config`: canonical JSON (§8.3); defaults made
explicit (a default and an explicitly passed equal value fingerprint
identically); semantically unordered collections sorted; fields that have no
effect on output removed.

Excluded: timestamps, job ids, pipeline run ids, worker host, output root
URIs, execution limits that do not change unit semantics, and Git state
unless it is a deliberate semantic input. `ExternalDatasetRef.uri` is also
excluded because it is location, not identity.

Note on `max_*` limits: a limit that truncates the produced unit set
(e.g. `max_built_scenes`) **does** change the output and must be part of
`build_config`.

### 15.3 Semantics-version discipline

A producer must bump `semantics_version` whenever unchanged inputs and
configuration could produce semantically different outputs: changed
segmentation logic, sampling, channel mapping, payload transformation,
annotation handling, or manifest schema meaning.

**Known limitation:** this discipline is manual. No mechanism detects a
producer change that should have bumped `semantics_version` but did not.
Code review and producer-level golden tests are the only guards. A stale
fingerprint makes a semantically changed rebuild look like "same fingerprint
→ reuse", so the old canonical set is kept.

---

## 16. DatasetVersion responsibilities

```text
DatasetVersion
├── SceneRecord[]
└── EpisodeRecord[]
```

DatasetVersion means **canonical membership and scope**. Its TARGET columns
are:

```text
id, dataset_id, version            identity
status                             DatasetVersionStatus (registered; unchanged)
scene_count, sample_count,
frame_count, episode_count,
observed_channels                  cached summary projections
created_at, updated_at
```

DatasetVersion must not own:

| Concern | Belongs to |
|---|---|
| raw source paths (`raw_source_root_uri`) | unit provenance (manifest) / builder input |
| recording URIs | recording ArtifactRecord |
| RobotRun lifecycle / RobotRun membership | RobotRunRecord (not a member) |
| `required_channels` | pipeline / builder / derived-workflow configuration |
| source format (`Dataset.type = nuscenes/…`, `source_dataset_*`) | `ExternalUnitSource` on each unit |
| dataset manifest URI (`manifest_uri`) | derived dataset-index ArtifactRecord |
| Scene manifest URIs | SceneRecord.manifest_artifact_id |
| quality / readiness cache (`latest_validation_run_id`, `validation_status`, `should_block_pipeline`, `validation_report_uri`, `latest_profile_run_id`, `profile_report_uri`) | validation / profile run records; readiness derived |

**Summary rule.** Summary counts are cached projections. Registrars are their
only writers. Registrars recompute them from canonical membership, under the
DatasetVersion row lock, in the same transaction as the membership change.
Builders, ingestors, validators, profilers and API services never write them.

**Unit scoping.** Each Scene/Episode record belongs to exactly one
DatasetVersion. Unit identity is DatasetVersion-scoped (§18.1). Sharing one
record across versions is not modeled. A new version that contains "the same"
unit gets its own record, and that record may reference the same immutable
manifest artifact.

`Dataset.type` is reworked or removed so that a Dataset does not carry a
source format. A DatasetVersion may contain units from several sources and
both source kinds.

---

## 17. Four ingestion pipeline contracts

### 17.1 `EXTERNAL_SCENE_INGESTION`

> **Superseded by A4 (§29.2).** Not introduced. A nuScenes scene reaches
> SceneOps as a recording (§29.13) and is built by
> `RECORDING_SCENE_BUILDING`.

```text
input     dataset_id, dataset_version, ExternalDatasetRef, ingest config, replace?
tasks     INGEST_EXTERNAL_SCENES → REGISTER_SCENES [→ VALIDATE_SCENE, PROFILE_SCENE]
source    an external dataset that already contains Scenes (e.g. nuScenes scenes)
produces  SceneManifest[] (ExternalUnitSource) → SceneRecord[]
identity  scope per unit: (DV, scene, external_format, source_unit_key)  (§18.2)
```

The integration runtime imports referenced sensor payloads into
SceneOps-owned storage (§13.9, §20.2) and writes canonical, checksum-qualified
manifests. The worker verifies the produced artifacts, registers their
ArtifactRecords, computes the fingerprint, and hands manifest artifact ids
to the registrar.

### 17.2 `EXTERNAL_EPISODE_INGESTION`

> **Superseded by A4 (§29.2).** Not introduced. An external Episode dataset
> used as test data becomes one recording per source episode (§29.13) and is
> built by `RECORDING_EPISODE_BUILDING`. The LeRobot EXPORT is an L3
> interoperability workflow and is unaffected.

```text
input     dataset_id, dataset_version, ExternalDatasetRef, ingest config, replace?
tasks     INGEST_EXTERNAL_EPISODES → REGISTER_EPISODES [→ VALIDATE_EPISODE, PROFILE_EPISODE]
source    an external dataset that already contains Episodes (e.g. LeRobot episodes)
produces  EpisodeManifest[] (ExternalUnitSource) → EpisodeRecord[]
identity  scope per unit: (DV, episode, external_format, source_unit_key)
```

CURRENT IMPLEMENTATION: does not exist. LeRobot is EXPORT-only. This
pipeline requires an INGEST capability in the isolated LeRobot runtime
(`tools/lerobot-integration`) and the Episode generalization from step 8.

### 17.3 `RECORDING_SCENE_BUILDING`

```text
input     dataset_id, dataset_version, robot_run_id (exactly one), build config, replace?
tasks     BUILD_RECORDING_SCENES → REGISTER_SCENES [→ VALIDATE_SCENE, PROFILE_SCENE]
source    resolve_recording(robot_run_id)
produces  SceneManifest[] (RecordingSegmentSource) → SceneRecord[]
identity  scope per recording: (DV, scene, recording, robot_run_id)  (§18.3)
```

Build configuration owns the mapping from source channel to modality and
sensor identity (the source channel identity is kept, §13.8), channel
inclusion (a boundary decision, §13.5) and segmentation. Lossy sampling,
frame association and pose interpolation are not canonical build steps
(§13.6). CURRENT IMPLEMENTATION:
`RAW_LOG_SCENE_BUILDING` builds from a `RawLogManifest`, by default the
`NUSCENES_RAW_LOG_MOCK` source. A recording that carries real camera/lidar
payloads in MCAP is a prerequisite for this pipeline (§24).

### 17.4 `RECORDING_EPISODE_BUILDING`

```text
input     dataset_id, dataset_version, robot_run_id (exactly one), build config, replace?
tasks     BUILD_RECORDING_EPISODES → REGISTER_EPISODES [→ VALIDATE_EPISODE, PROFILE_EPISODE]
source    resolve_recording(robot_run_id)
produces  EpisodeManifest[] (RecordingSegmentSource) → EpisodeRecord[]
identity  scope per recording: (DV, episode, recording, robot_run_id)
```

Build configuration owns observation/action channel mapping (e.g. which
robot-state fields are actions), segmentation strategy, and control
frequency. The source clock comes from the RobotRun, not from a default.
CURRENT IMPLEMENTATION: `RAW_LOG_EPISODE_BUILDING` with `BUILD_EPISODES`.
It accepts `robot_run_id` **or** `mcap_uri`, and hard-codes
steering/throttle/brake as actions.

> **Amended by A6 (§31).** Implemented as stated, except that a canonical
> Episode has no control frequency (a target frequency is an `ALIGN_EPISODE`
> choice) and the window clock is the segmentation clock the build
> configuration declares (§30.2 applied to Episodes), not the RobotRun's.

### 17.5 Rules common to all four

1. **One source per pipeline run.** Recording pipelines take exactly one
   `robot_run_id`. A DatasetVersion that contains many RobotRuns is built
   through many pipeline runs. The pipeline is not made multi-recording for
   convenience. Multi-recording orchestration is DEFERRED.
2. **Registrar ownership.** Registrars are the only writers of canonical
   SceneRecord/EpisodeRecord membership and DatasetVersion summaries.
   Builders and ingestors produce manifests and artifacts. Validators and
   profilers produce run records. None of them redefine membership.
3. **Fail loudly.** If an expected manifest cannot be read, fails its
   checksum, fails strict parsing, or disagrees with the declared scope or
   fingerprint, registration fails as a whole and makes no canonical change.
   Units are never silently skipped. (CURRENT: `RegisterScene` and
   `RegisterEpisode` `continue` on a missing manifest. This is removed.)
4. **Recording-scope completeness.** A recording builder hands the registrar
   the **complete** unit set for its scope, together with the fingerprint.
   The registrar treats it as the whole new state of that scope (§18.3).
5. **Validation never gates membership.** Validation and profiling run after
   registration. A blocking validation stops later pipeline tasks. It never
   removes or prevents canonical membership. Readiness exposes the result.
6. **DatasetVersion must exist.** Pipelines never create DatasetVersions
   implicitly.
7. **Derived steps are not ingestion.** Dataset index / dataset manifest
   generation is a derived workflow that produces an ArtifactRecord. It is
   not a task that canonical membership depends on.

### 17.6 Pipeline / job vocabulary (TARGET names)

```text
PipelineType                  JobTypes
EXTERNAL_SCENE_INGESTION      INGEST_EXTERNAL_SCENES, REGISTER_SCENES
EXTERNAL_EPISODE_INGESTION    INGEST_EXTERNAL_EPISODES, REGISTER_EPISODES
RECORDING_SCENE_BUILDING      BUILD_RECORDING_SCENES, REGISTER_SCENES
RECORDING_EPISODE_BUILDING    BUILD_RECORDING_EPISODES, REGISTER_EPISODES
(standalone)                  REGISTER_ROBOT_RUN
```

Exact enum spellings may be adjusted during implementation. The four-way
taxonomy and the single-registrar-per-domain structure may not.

---

## 18. Canonical identity and replacement invariants

### 18.1 Deterministic unit identity

```text
external unit id   = f(dataset_id, dataset_version, domain, external_format, source_unit_key)
recording unit id  = f(dataset_id, dataset_version, domain, robot_run_id, unit_key)
```

Ids are deterministic, so retries converge and identities can be reused
across replacements when the unit key is unchanged. (CURRENT: Scene ids are
already DatasetVersion-scoped, per commit 0e797a7.)

### 18.2 External units

> **Superseded by A4 (§29.9).** External units do not exist. Recording-derived
> identity (§18.1, second line) and recording-scope replacement (§18.3) are
> the only identity rules.

Scope: `(DatasetVersion, domain, external_format, source_unit_key)`, i.e. a
single unit.

| Existing unit in scope | New fingerprint / manifest checksum | `replace` | Result |
|---|---|---|---|
| none | — | any | register |
| exists | same fingerprint **and** same checksum | any | no-op |
| exists | different fingerprint or checksum | false | **fail**, no canonical change |
| exists | different fingerprint or checksum | true | move that unit to the new revision (same identity; repoint `manifest_artifact_id`, update projections) |

External ingestion is additive per unit. A unit missing from a later
ingestion run is **not** deleted. Explicit removal of an external unit from a
DatasetVersion is DEFERRED.

### 18.3 Recording-derived units

Scope: `(DatasetVersion, domain, source_kind=recording, robot_run_id)`.

Invariant: **one scope has exactly one current producer fingerprint.** Every
record in the scope carries it.

| Current scope | New fingerprint | `replace` | Result |
|---|---|---|---|
| empty | — | any | register complete new set |
| non-empty | same | any | reuse current complete set; no-op |
| non-empty | different | false | **fail**, no canonical change |
| non-empty | different | true | atomically replace the complete set |

Replacement runs in **one registrar transaction under the DatasetVersion row
lock**:

```text
1. acquire DatasetVersion lock; RE-EVALUATE the scope's current fingerprint
   (another build may have committed while this one was building)
2. validate the complete new set (all manifests readable, verified, in scope,
   one fingerprint)
3. delete current records in the scope whose ids are absent from the new set
4. repoint/update records whose ids are reused (unit_key unchanged)
5. insert new records
6. recompute DatasetVersion summaries from membership
7. commit
```

After commit, old and new canonical sets **never** coexist. Old immutable
manifests and artifacts are retained.

Concurrency: builds run outside the lock because building is expensive.
Registrations for the same DatasetVersion serialize on the row lock. A
registrar that finds, after acquiring the lock, that the scope already has
its fingerprint converges to a no-op. One that finds a *different*
fingerprint applies the table above against that newly committed state.

Empty result: in v1 a recording build that yields zero units **fails**. An
empty scope cannot carry a fingerprint, so "same fingerprint" would be
undetectable. If empty results become legitimate, a per-scope build record
is needed (§26).

### 18.4 RobotRun

No replacement semantics.

```text
same RobotRunManifest checksum for run_id       → idempotent no-op
different manifest checksum for the same run_id → hard conflict
```

### 18.5 Effect on derived records

Deleting or repointing a canonical unit does not rewrite history:

- Validation and profile run records are append-only history. Readiness
  counts only run records for the current manifest revision (§13.4).
- Derived workflows (ScenarioSet, AlignedEpisode, LearningDataExport,
  Inference, Evaluation) must pin the manifest artifact id and checksum they
  consumed, as aligned-episode and learning-export params already do with
  `aligned_artifact_id` / `*_checksum`. They must not assume a referenced
  unit id still exists or still points at the same revision.

---

## 19. Immutable manifests and artifact keys

1. Manifests are immutable.
2. Manifest artifact keys are write-once. Writing to an existing key is
   allowed only when the bytes are identical, in which case it is a no-op.
3. Scene and Episode manifest keys are **checksum-qualified**, so several
   historical revisions of the same unit coexist without overwrite, e.g.
   `…/{dataset_id}/{version}/scenes/{scene_id}/manifest-{sha256}.json`.
   (CURRENT: `{scene_manifest_root}/{scene_id}.json`, overwritten on rebuild.)
4. `RobotRunManifest` uses a **fixed per-run key**, which is allowed only
   because re-registration with changed bytes is prohibited (§18.4). The
   recording key is also fixed per run and write-once.
5. Imported payload artifacts (sensor frames, videos, Parquet) use
   deterministic, write-once keys. Content-qualified keys are used where one
   logical payload may legitimately have several byte versions.
6. Old manifests and artifacts stay available for lineage and history, even
   when no current canonical record references them.
7. Garbage collection of unreferenced artifacts is DEFERRED. Until it
   exists, storage grows monotonically with replacements.

---

## 20. Source independence and external-format extensibility

### 20.1 Target property

> After canonicalization, downstream workflows do not need to know whether a
> Scene or Episode originated from nuScenes, LeRobot, ROS2, Kafka, MCAP, or
> any future integration.

Downstream code may read `source_kind` and provenance for display, lineage
and audit. It may not branch its **processing semantics** on source format.
It also never needs the original external dataset's layout, location or SDK.
Canonical manifests and SceneOps-owned artifacts are sufficient (§13.9).

### 20.2 Current leakage that implementation must remove

| Category | CURRENT example | TARGET home |
|---|---|---|
| External-root-dependent payload URIs | Scene frame `uri` pointing into the nuScenes dataroot | Integration imports payloads into SceneOps-owned ArtifactStore keys; manifests reference only those |
| Source-specific binary assumptions | nuScenes `.pcd.bin` lidar layout assumed in detection (e.g. frustum lifting) | Payload `format` / `media_type` declared per frame in the manifest; consumers dispatch on declared format |
| Hard-coded channel names | `CAM_FRONT` / `LIDAR_TOP` in `constants/sensors.py`, `scenes/schemas/sampling.py`, detection params | Builder config (sampling) and derived-workflow config (detection); no source channel names in core defaults |
| Robot-specific Episode channels | `steering` / `throttle` / `brake` as fixed actions in `episode_builder.py` | `RECORDING_EPISODE_BUILDING` build config (part of the fingerprint) |
| Source clock defaults | `MCAP_LOG_TIME_CLOCK` as the assumed clock | `RobotRunManifest.capture.source_clock` → `RecordingSegmentSource.source_clock` → consumers read it from provenance |
| Source-specific lineage | `raw_log_id`, `source_dataset_*`, `SceneGenerationMethod.RAW_LOG/DATASET`, `RawLogSourceType` | `ExternalUnitSource` / `RecordingSegmentSource` |
| Source format on dataset identity | `Dataset.type = nuscenes/waymo/kitti` | Per-unit `ExternalUnitSource.revision.format` |
| Source paths on DatasetVersion | `raw_source_root_uri`, `required_channels` | Builder input / pipeline config |

Each concern moves to exactly one of: **adapter** (integration runtime),
**manifest contract**, **builder configuration**, or **derived workflow
configuration**. None moves to DatasetVersion or canonical identity.

### 20.3 External formats are integrations, not domain types

> **Superseded by A4 (§29.13) for ingestion, §20.3–§20.7.** A new external
> dataset format adds an *adapter in the external acquisition tool*, which
> emits a recording. It no longer adds a SceneOps integration that maps onto
> canonical manifests, and it adds nothing to SceneOps core. §20.4's open
> format identifiers and §20.5's integration boundary remain only for
> interoperability runtimes (the LeRobot EXPORT). §20.1, §20.6 and the
> "no universal adapter" rule stand.

> **External formats are integration implementations, not SceneOps core
> domain types.**

Current reference integrations:

```text
nuScenes   → Scene-native external source    (EXTERNAL_SCENE_INGESTION)
LeRobot    → Episode-native external source  (EXTERNAL_EPISODE_INGESTION)
```

Other Scene-native or Episode-native formats may be added later. Adding one
normally requires:

```text
a new integration package / runtime (isolated, DB-free, own SDK)
a mapping from that format to the existing SceneManifest or EpisodeManifest
integration registration / configuration (format identifier → runtime)
mapping / contract tests (source fixture → expected canonical manifest)
```

and normally does **not** require:

```text
a new DatasetVersion schema
a new SceneRecord / EpisodeRecord schema
a new PipelineType or JobType
a new core data-unit abstraction
a new downstream workflow implementation
```

The exception is a source that exposes genuinely new domain semantics, which
the canonical contract cannot represent without loss. In that case the
**canonical contract** is amended. That is a domain decision, and the new
fields must be expressible by any source, not only the format that prompted
them. The format does not get its own canonical type.

### 20.4 Open format identifiers

External formats are identified by open values, not by a closed core enum
such as `NUSCENES | WAYMO | KITTI | LEROBOT`:

```text
ExternalDatasetRef
  format              open identifier of the integration implementation (e.g. "nuscenes", "lerobot")
  format_version      that format's own version
  uri / repository    location: an integration-side locator; never identity and
                      never canonical provenance (§14.3, §15.2, §28)
  external_revision   source revision
```

`format` selects the integration implementation. It does not make each
supported dataset a SceneOps domain concept. The `external_format`
projection on records (§13.3) and the format component of identity
(§18.1) and fingerprint (§15.2) carry the same open value. Format
identifiers are stable once units are registered under them. Renaming one
changes external unit identity.

SceneOps-owned contracts may still use closed sets where SceneOps defines the
meaning and versions it through `schema_version`, for example
`RobotRunManifest.recording.format` and `capture.source.kind` (§8.2). These
describe SceneOps's own publication contract. They are not external
integration identifiers.

CURRENT IMPLEMENTATION: `ExternalDatasetRef.format` is already an open
`str`. The CURRENT IMPLEMENTATION DEBT is closed-enum dispatch elsewhere:

- `IngestScenesJobParams.source_format: DatasetType` (default `NUSCENES`),
  dispatched by `if params.source_format == DatasetType.NUSCENES` in
  `jobs/dataset/ingest_scenes.py`;
- `DatasetType` (`nuscenes` / `waymo` / `kitti` / `custom`) on `Dataset.type`
  (§16);
- `RawLogSourceFormat` (`nuscenes` / `rosbag` / `folder` / `waymo` / `kitti` /
  `custom`), which is removed with the raw-log path (§22.4).

### 20.5 Integration boundary

```text
ExternalDatasetRef
       ↓
format-specific isolated integration        (layer A; may use the source SDK)
       ↓
source-preserving materialization           (§13.9)
       ↓
SceneManifest[]  OR  EpisodeManifest[]
       ↓
canonical registration                      (REGISTER_SCENES / REGISTER_EPISODES)
       ↓
SceneRecord[]    OR  EpisodeRecord[]
```

The integration may use source-specific SDKs, and SceneOps core never
requires them. The boundary is serialized and SDK-independent: it consists
of `IntegrationRequest` / `IntegrationResult`, canonical manifests and
artifacts. Mapping source semantics onto the canonical contract, including
source channel → modality / sensor identity (§13.8), is the integration's
responsibility. Mapping tests prove it.

The mechanism that resolves a `format` to its integration runtime in v1 is
configuration or static registration in the integration/application layer,
keyed by `(domain, format)` (§27.6). A dynamic plugin registry is DEFERRED
(§26).

### 20.6 No universal adapter or data unit

> Platform primitives are generic; domain semantics remain explicit.

The following are **not** introduced: `UniversalDataUnit`,
`UniversalObservation`, `UniversalBuilder`, a universal adapter, or one
model for both Scene and Episode. Shared primitives are allowed:

```text
ExternalDatasetRef · ArtifactRef
ExternalUnitSource · RecordingSegmentSource · ProducerInfo
source-clock and payload-format / encoding primitives
integration runtime contracts (IntegrationRequest / IntegrationResult)
```

Scene and Episode remain distinct canonical domain models with their own
manifests, records, builders and registrars (§3.3, §13.1).

### 20.7 Convergence

Both source kinds converge on one manifest contract per domain before
registration (§6.7). Downstream workflows operate on canonical contracts,
never on source-format identity.

---

## 21. Explicit invariants

> **Amended by A4 (§29.18).** I-25 is superseded. I-28 and I-30 are narrowed.
> I-31–I-37 are added.

```text
I-1   Every SceneRecord / EpisodeRecord references exactly one manifest ArtifactRecord
      (manifest_artifact_id) whose bytes verify against its checksum.
I-2   The current revision of a unit is the manifest named by manifest_artifact_id;
      never "latest by created_at".
I-3   Manifests and their keys are immutable / write-once; same-key rewrite only for
      byte-identical content.
I-4   A RobotRunRecord exists iff a canonical RobotRunManifest was registered whose
      recording verified (checksum, size, format) at registration time.
I-5   RobotRunRecord is immutable and has no lifecycle status, no dataset membership,
      no recording URI.
I-6   RobotRunRecord is never a DatasetVersion member.
I-7   No canonical path accepts a recording source other than resolve_recording(robot_run_id).
I-8   Layer-A components (integrations, capture, publisher) never import sceneops-db
      or write Records.
I-9   Registration never uploads, moves or rewrites recording bytes.
I-10  Registrars are the only writers of Scene/Episode canonical records, membership,
      and DatasetVersion summaries; summaries are recomputed under the DatasetVersion lock.
I-11  A registration either applies its complete input or makes no canonical change;
      units are never silently skipped.
I-12  A recording-derived scope has exactly one current producer_fingerprint; old and
      new canonical sets never coexist after commit.
I-13  Identical normalized inputs → byte-identical RobotRunManifest; registration
      rejects non-canonical manifest bytes.
I-14  Same run_id + different manifest checksum → hard conflict, never replace.
I-15  Robot platform conflicts fail registration; never last-write-wins.
I-16  SceneRecord / EpisodeRecord have no status; record existence = registered;
      readiness is derived from run records for the current manifest revision.
I-17  DatasetVersion holds membership and recomputed summaries only.
I-18  Canonical identity never depends on external storage location (URIs, roots).
I-19  Recording pipelines operate on exactly one robot_run_id per run.
I-20  Source independence after canonicalization: downstream workflows use canonical
      semantics only; they do not branch processing semantics on source format and
      never require the original external dataset layout, location or SDK.
I-21  Source fidelity: within a selected canonical domain boundary, source observations
      and their interpretation-critical semantics (payload, source timing and clock,
      source identity, calibration, frame semantics, poses, annotations, provenance)
      remain reconstructable from the canonical manifest and its SceneOps-owned artifacts.
I-22  Lossy / workflow-specific transformations (resampling, temporal alignment,
      interpolation, frame association, sensor synchronization / fusion, model-specific
      preprocessing) are derived, unless they intrinsically define the canonical domain
      unit; a domain-defining transformation is recorded in ProducerInfo and does not
      replace the observations it interprets.
I-23  Canonical observations keep their own source timestamps and source channel
      identity; canonical semantic fields (modality, sensor identity) are added
      alongside them, never substituted with another format's vocabulary.
I-24  Canonical payload references resolve only to SceneOps-owned, checksum-verified
      artifacts; no canonical manifest or record depends on an external dataset root.
I-25  A new external source format normally adds an integration (runtime, mapping,
      registration / configuration, mapping tests), not a new core canonical type,
      record or DatasetVersion schema, PipelineType, or downstream workflow; external
      format identifiers are open values, not core enums.
I-26  Canonical source timestamps are integer nanoseconds in a declared source clock;
      (timestamp_ns, source_clock) together define temporal meaning. No floating-point
      value and no imposed UTC interpretation participates in source-time identity.
I-27  producer_fingerprint is computed only from the source revision, producer_id,
      semantics_version and normalized build_config. It never includes execution state
      (job / pipeline run ids, execution timestamps, hosts, paths) or the output
      manifest checksum.
I-28  External format, source clock and producer identifiers are canonical open
      identifiers. They are validated, never normalized, once they enter a contract.
I-29  Canonical observation payloads resolve to SceneOps-owned payload artifacts for
      both source kinds; no canonical consumer parses a source recording or an
      external dataset to read a canonical observation.
I-30  Source locations and display names never appear in canonical provenance: the
      same source revision, unit, producer and build configuration produce
      byte-identical canonical manifests wherever, and under whatever name, the source
      was read (A3, §28).
```

Each invariant should be backed by at least one test at the layer that can
prove it (§24): unit tests for serialization and rules, real PostgreSQL for
I-10/I-11/I-12/I-14 concurrency, and real MinIO for I-3/I-4/I-9/I-24.
I-21–I-23 are backed by golden-manifest tests per producer and mapping /
contract tests per integration (source fixture → expected canonical
manifest). I-20 and I-25 are backed by import-boundary tests, which check
that SceneOps core does not depend on any integration SDK. I-26–I-28 are
backed by contract tests on the step-4 primitives; I-29 by the step-5 and
step-8 producer tests; I-30 by provenance and manifest contract tests that
canonicalize one source revision from different locations and display names.

---

## 22. Concepts, APIs and fields to remove or change

Actions: **KEEP** (unchanged), **RENAME**, **REWORK** (same purpose, new
contract), **MERGE**, **REMOVE**.

### 22.1 RobotRun / recording

| Item | Action | Target |
|---|---|---|
| `POST /robot-runs` (bare create), `CreateRobotRunRequest` | REMOVE | `POST /robot-runs:register` (job-backed) |
| `sceneops-worker robots register-run` | REMOVE | — |
| `sceneops-worker robots register-capture` / `register_robot_run_capture` | REWORK (split) | Publisher (DB-free, P1–P5) + `REGISTER_ROBOT_RUN` |
| `RobotRun.status`, `RobotRunStatus` (incl. `INGESTED`) | REMOVE | existence = finalized + verified |
| `RobotRun.dataset_id`, `dataset_version`, `raw_log_id` | REMOVE | — |
| `RobotRun.mcap_uri`, `rosbag_uri` | REMOVE | recording ArtifactRecord |
| `RobotRun.metadata`, `updated_at` | REMOVE | manifest / immutable |
| `RobotRunRecord` | REWORK | §10.2 |
| `RobotRunManifest` | NEW | §8 |
| `JobType.REGISTER_ROBOT_RUN` | NEW | §12.1 |
| `robot_run_recording_artifact_id` | KEEP | + `robot_run_manifest_artifact_id` (NEW) |
| `materialize_recording` | REWORK | `resolve_recording(robot_run_id)` |
| `is_local_uri` / `verify_local_recording_checksum` local branch | REMOVE | ArtifactStore-only path |
| `mcap_uri` on `BuildEpisodesJobParams`, `IngestRobotStatesJobParams` | REMOVE | `robot_run_id` only |
| `RobotRunNotMaterializedError` | REWORK | resolver error: "RobotRunRecord missing" (a RobotRun without a recording artifact can no longer exist) |
| `ArtifactKind.ROBOT_RUN_RECORDING` | KEEP | + `ArtifactKind.ROBOT_RUN_MANIFEST` (NEW) |
| Robot platform hard-coded default / upsert | REMOVE | §9 rule |

### 22.2 DatasetVersion / Dataset

| Item | Action | Target |
|---|---|---|
| `DatasetVersion.raw_source_root_uri` | REMOVE | builder input |
| `DatasetVersion.required_channels` | REMOVE | pipeline / workflow config |
| `DatasetVersion.manifest_uri` | REMOVE | derived index ArtifactRecord |
| `DatasetVersion.source_dataset_id` / `source_dataset_version` | REMOVE | `ExternalUnitSource` |
| `DatasetVersion` quality cache (6 columns) | REMOVE | derived readiness |
| `DatasetVersion.channels` | RENAME/REWORK | `observed_channels`, registrar-recomputed |
| `update_scene_summary` callers outside registrar (build_scenes, API service) | REMOVE | registrar only |
| `Dataset.type` / `DatasetType` (`nuscenes`/`waymo`/`kitti`) | REWORK or REMOVE | no source format on Dataset |
| `DatasetAssembler` / `DatasetValidator` / `DatasetProfiler` Protocols (`datasets/contracts.py`, zero implementations) | REMOVE | — |

### 22.3 Scene / Episode

| Item | Action | Target |
|---|---|---|
| `SceneRecord.status` column + `SceneStatus` enum | REMOVE | record existence = registered |
| `EpisodeRecord.status` column + `EpisodeStatus` enum | REMOVE | record existence = registered |
| validate/profile mutation of Scene status | REMOVE | run records + derived readiness only |
| status filters/fields in Scene/Episode API responses and indexes (`ix_scenes_status`, `ix_episodes_status`) | REMOVE | readiness views |
| `scene_manifest_uri` / `episode_manifest_uri` on records | REWORK | `manifest_artifact_id` |
| `SceneRecord.raw_log_id`, `segment_id`, `lineage`, `generation`, `artifact_root_uri` | REMOVE | provenance in manifest + projections |
| `EpisodeRecord.raw_log_id` (+ `mission_id`/`robot_id` as identity) | REMOVE | `RecordingSegmentSource` |
| `origin_type` / `generation_method` / `SceneGenerationMethod` / `SceneOriginType` | REWORK | `source_kind` + `ProducerInfo` |
| `SceneLineage`, `EpisodeLineage` | REWORK | §14.2 |
| Scene manifest key `{scene_id}.json` | REWORK | checksum-qualified key |
| Register handlers' silent `continue` | REMOVE | fail loudly |
| `RegisterScene*` / `RegisterEpisode*` | RENAME/REWORK | `REGISTER_SCENES` / `REGISTER_EPISODES` registrars with §18 semantics |
| WorldState surface (`scenes/schemas/world_state.py`, `build_world_state`, `world_state_manifest_uri`, `SceneManifest.world_state` / `world_state_uri`, `SceneAssetKind.WORLD_STATE`) | REMOVE | — (no reader or writer exists) |
| `SceneBuilder`, `EpisodeBuilder` | KEEP (separate) | fed by `resolve_recording` + build config |
| Lossy sample grouping / association / downsampling and per-frame ego-pose resolution inside canonical Scene construction (`SampleGroupingConfig`, `scenes/building/association.py`, `resolvers.py`) | REWORK | derived transformation (§13.6); canonical manifest keeps every in-boundary observation |
| `SceneSensorFrameManifest.channel` as the only channel identity | REWORK | source channel identity + canonical modality / sensor identity (§13.8) |
| nuScenes channel sets as core defaults (`constants/sensors.py`, `anchor_channel="LIDAR_TOP"`) | REMOVE | integration mapping / build or workflow config |

### 22.4 Raw-log / nuScenes legacy

| Item | Action | Target |
|---|---|---|
| nuScenes raw-log mode (`mode=raw_log`, `NuScenesRawLogMocker`, `sceneops_integrations/nuscenes/raw_log.py`) | REMOVE | nuScenes is EXTERNAL_SCENE_INGESTION only |
| `RawLogSourceType` (incl. `NUSCENES_RAW_LOG_MOCK`), `RawLogSourceFormat` | REMOVE | `source_kind` + `external_format` / `recording.format` |
| `RawLogManifest`, `RawLogFrameIndex` as platform contracts | REMOVE | builder-internal intermediate (not canonical; not an API) |
| `RawLogAdapter` Protocol + `RawLogAdapterFactory` | REMOVE | recording builders read via `resolve_recording`; MCAP decoding is builder-internal |
| `generate_raw_log_id`, `raw_log_id` params | REMOVE | — |
| `IngestScenes` (`mode=scene_manifest`) | RENAME/REWORK | `INGEST_EXTERNAL_SCENES`, payload import (§13.9, §20.2) |
| `IngestScenesJobParams.source_format: DatasetType` + closed `NUSCENES` dispatch | REWORK | `ExternalDatasetRef.format` (open) → configured integration (§20.4) |
| `BuildScenes` | RENAME/REWORK | `BUILD_RECORDING_SCENES` |
| `BuildEpisodes` | RENAME/REWORK | `BUILD_RECORDING_EPISODES` |

### 22.5 Pipelines / jobs

| Item | Action | Target |
|---|---|---|
| `PipelineType.DATASET_SCENE_INGESTION` | RENAME/REWORK | `EXTERNAL_SCENE_INGESTION` |
| `PipelineType.RAW_LOG_SCENE_BUILDING` | RENAME/REWORK | `RECORDING_SCENE_BUILDING` |
| `PipelineType.RAW_LOG_EPISODE_BUILDING` | RENAME/REWORK | `RECORDING_EPISODE_BUILDING` |
| — | NEW | `EXTERNAL_EPISODE_INGESTION` |
| `PipelineType.SCENE_REGISTRATION` (register arbitrary manifests) | REMOVE | bypasses provenance; generated/simulated Scenes DEFERRED |
| `BUILD_SCENE_INDEX` + `BUILD_DATASET_MANIFEST` (duplicate derived outputs) | MERGE | one derived dataset-index job producing an ArtifactRecord; removed from ingestion pipelines' canonical path |
| Reserved handler-less JobTypes `COMPARE_SCENES`, `AUTO_LABEL_SCENE`, `EXPORT_SCENE_PACKAGE`, `AUTO_LABEL_DATASET`, `EXPORT_DATASET` + their reserved `ArtifactOwnerType` / `ArtifactKind` values | REMOVE | reintroduce with an implementation |
| `INGEST_ROBOT_STATES`, `EXPORT_ROBOT_ANALYTICS_SNAPSHOT` | REWORK | `robot_run_id` + resolver only |
| `SCENARIO_CURATION`, `DETECTION_EVALUATION`, alignment / export / curation jobs | KEEP (step 10) | migrate only canonical-boundary reads (status, DV cache, channel defaults) when earlier steps break them |

### 22.6 E2E / baseline

| Item | Action | Target |
|---|---|---|
| `e2e_scene_rawlog.sh` (fake raw-log) | REMOVE | recording-scene E2E |
| `e2e_robot_run_registration.sh`, `robot_run_registration_verify.py` | REWORK | publisher + `REGISTER_ROBOT_RUN` E2E |
| `e2e_episode_building.sh`, `e2e_robot_learning.sh`, `e2e_robot_run_learning.sh`, `e2e_robot_can_replay.sh` | REWORK/MERGE | recording-episode E2E via resolver |
| `e2e_scene.sh` | REWORK | external-scene E2E with payload import |
| — | NEW | external-episode E2E (LeRobot INGEST) |
| `scripts/canonical/canonical_bootstrap.sh`, canonical baseline v0.0 | REWORK | new canonical baseline from the four pipelines |
| `register_nuscenes_dataset.sh`, `scripts/e2e/lib.sh` helpers using `register-run` / `mcap_uri` | REWORK | new registration paths |

The tables list candidates the audit identified. If implementation finds
another surface that violates §21, it is removed under the same policy and
reported. It is not preserved.

---

## 23. Breaking-change policy

1. **No compatibility shims.** Old APIs, CLI commands, JobTypes,
   PipelineTypes and fields are deleted, not aliased or deprecated. A shim is
   allowed only with a concrete architectural purpose stated in its PR.
2. **Schema migrations may be destructive.** Columns and enum values listed
   in §22 are dropped. Migrations do not have to preserve existing dev data.
3. **Dev-state reset is acceptable.** Local PostgreSQL, MinIO buckets, Redis
   and Kafka topics may be reset. The canonical baseline is regenerated from
   the new pipelines rather than migrated.
4. **E2E flows are replaced, not kept green on legacy paths.** Each step
   replaces the E2Es it breaks. Step 11 consolidates them.
5. **One authoritative path.** At no point after a step lands may old and new
   paths for the same capability coexist on `main`.
6. **Correctness is not relaxed.** Breaking compatibility does not permit
   weaker failure semantics, idempotency, lineage or verification.
   Historical benchmark and acceptance records are preserved (category C
   documents are not rewritten).
7. **Active documentation follows each step.** Active architecture docs are
   updated to the then-current state when a step changes a contract. This
   ADR is not edited to track progress; it is superseded or amended only if a
   decision changes.

---

## 24. Implementation sequence

> **Amended by A4 (§29.19).** Steps 0–5 are complete. Steps 6–11 below are
> superseded by the acquisition-first sequence in §29.19, and are kept as
> the historical plan.

Each step ends with focused unit tests, the relevant real-infrastructure
tests (PostgreSQL, MinIO, and Kafka/ROS2 where touched), and replacement of
any E2E it breaks.

```text
0. ADR freeze
   - this document

1. RobotRunManifest contract + Recording Publisher + REGISTER_ROBOT_RUN
   - RobotRunManifest v1 schema + canonical serializer (sceneops-core)
   - DB-free publisher (P1–P5); placement decided; import-boundary test
   - REGISTER_ROBOT_RUN job + POST /robot-runs:register + CLI
   - robot platform rule
   - tests: determinism (byte-identical retries), strict parse, canonical-form
     rejection, conflict, concurrent registration on real PostgreSQL,
     write-once + verification on real MinIO, crash-before-manifest

2. Shared verified recording resolver
   - resolve_recording(robot_run_id); remove local-path branch
   - migrate BUILD_EPISODES and INGEST_ROBOT_STATES to it; drop mcap_uri params

3. RobotRun schema/API cleanup
   - drop status/dataset/raw_log/URI/metadata columns; RobotRunRecord §10.2
   - remove POST /robot-runs, register-run, DB-coupled register-capture
   - replace robot-run registration E2E

4. Canonical source/provenance contract freeze
   - domain-neutral primitives in sceneops-core: ExternalUnitSource,
     RecordingSegmentSource, ProducerInfo, producer_fingerprint; source-clock,
     source-channel-identity and payload-format/encoding primitives (§13.8, §20.6)
   - canonical JSON + checksum-qualified key rules for Scene/Episode manifests (§8.3, §19)
   - open external format identifier → configured integration selection (§20.4, §20.5)
   - resolve the open questions the Scene/Episode contracts depend on
     (timestamp precision, recording payload addressing, §13.9)
   - no Scene/Episode manifest schema change in this step

5. Canonical Scene representation refactor
   - concrete source-faithful SceneManifest schema meeting §13.5–§13.8
   - manifest_artifact_id on SceneRecord; REGISTER_SCENES registrar ownership
     + §18 identity/replacement + fail-loud
   - drop SceneRecord.status / SceneStatus; readiness from run records (current revision)
   - DatasetVersion cleanup (§16) except raw_source_root_uri (step 6);
     remove non-registrar summary writers
   - migrate canonical-boundary readers in derived workflows that break
     (DV quality cache, Scene status, sample-centric manifest reads)

6. External Scene ingestion normalization
   - EXTERNAL_SCENE_INGESTION: nuScenes integration maps to the step-5
     SceneManifest, materializes payloads into SceneOps-owned storage (§13.9),
     preserves source timing and channel identity
   - closed DatasetType dispatch replaced by format → integration (§20.4)
   - drop DatasetVersion.raw_source_root_uri; detection resolves payloads
     through canonical artifacts

7. Recording Scene building
   - RECORDING_SCENE_BUILDING: robot_run_id via resolver; channel → modality/sensor
     build config; segmentation without lossy sampling (§13.6)
   - sensor-bearing MCAP fixture (real camera/lidar payloads)
   - remove nuScenes raw-log mode and RawLog* in the same step

8. Canonical Episode generalization
   - apply step-4 primitives to Episode (provenance, fingerprint, registrar, keys)
   - source-faithful EpisodeManifest (§13.10), able to express external
     datasets (e.g. video-backed frames)
   - drop EpisodeRecord.status / EpisodeStatus
   - action/observation channel mapping and source clock as build config/provenance
   - RECORDING_EPISODE_BUILDING rename/rework

9. External Episode ingestion
   - EXTERNAL_EPISODE_INGESTION: LeRobot INGEST capability in the isolated runtime
   - INGEST_EXTERNAL_EPISODES job + E2E

10. Derived transformation cleanup after the ingestion architecture is stable
    - sampling / association / pose interpolation / synchronization live in
      derived workflows (§13.6); AlignedScene only if workflows converge (§13.11)
    - ScenarioSet / detection / alignment / export / curation on canonical
      semantics, provenance and manifest pinning; source-specific defaults removed
    - merge dataset index jobs; remove reserved JobTypes and dead Protocols

11. E2E consolidation + active docs + new canonical baseline
    - one E2E per pipeline; clean-room run; regenerated canonical baseline
    - docs/architecture/* confirmed current
```

Dependencies that force or allow reordering:

| Dependency | Consequence |
|---|---|
| Step 2 needs `RobotRunRecord.recording_artifact_id` from step 1 | 1 → 2 |
| Step 3 drops `mcap_uri`, which current consumers still read | 2 → 3 |
| Steps 5 and 8 build their manifests from step 4's primitives | 4 → 5, 4 → 8 |
| Steps 6 and 7 produce the SceneManifest that step 5 defines | 5 → 6, 5 → 7 |
| Step 5's removal of the DV quality cache, Scene status values and sample-centric manifest shape breaks scenario/detection readers | their canonical-boundary migration happens **inside** step 5, not in step 10 |
| Detection resolves nuScenes payloads only through `DatasetVersion.raw_source_root_uri`; SceneOps-owned payloads exist only after step 6 | that column is dropped in step 6, together with the payload-resolution migration, not in step 5 |
| Step 7 needs a recording with real sensor payloads; the CAN-replay recording has none | sensor-replay fixture is a prerequisite of step 7; if delayed, steps 8–9 may proceed before 7 |
| Removing nuScenes raw-log mode leaves `RAW_LOG_SCENE_BUILDING` without a source | removal is bundled with step 7, not done earlier |
| Step 9 needs EpisodeManifest generalized for external semantics | 8 → 9 |
| Step 5 changes manifest keys and identity, which invalidates the canonical baseline v0.0 | baseline regeneration is deferred to step 11; intermediate steps run on reset dev state |
| Step 10's reserved-JobType / Protocol removal has no dependencies | may be done at any point as an independent change |

---

## 25. Consequences and trade-offs

### Positive

- One model of canonical units across four sources, with explicit domain
  semantics kept separate.
- Recording publication can be retried and its integrity verified without a
  database. A crash cannot make an incomplete recording look published.
- RobotRun becomes trustworthy provenance: existence implies verification.
- Rebuilds are explicit. A configuration change cannot silently overwrite
  canonical data, and replacement is atomic.
- Lineage is complete and immutable. Any unit can be traced to the exact
  manifest revision, producer semantics and source bytes.
- Downstream workflows lose their source-specific branches.
- Fewer parallel paths: four pipelines, one resolver, one registrar per
  domain.
- Less source-format leakage. Sources keep their own channel vocabulary, and
  no source imitates another (§13.8).
- Canonical data can be reinterpreted later. A new workflow can choose its
  own alignment or association without rebuilding canonical units (§13.6).
- Derived transformations are reusable across sources because they consume
  one canonical contract per domain (§6.7).
- New external formats are easier to add: an integration plus mapping
  tests, with no core schema or pipeline change (§20.3).
- Provenance is more complete, down to observation-level source identity and
  timing.
- The boundary between canonical and derived is clear, and derived outputs
  are reproducible from canonical manifests.

### Negative / costs

- **Large breaking change.** Most ingestion code, many schemas, several APIs
  and most E2Es change. Dev state and the canonical baseline are regenerated.
- **Storage growth.** Payload import from external datasets duplicates bytes
  into SceneOps storage. Immutable manifests accumulate across replacements.
  GC is deferred.
- **Double verification cost.** Publisher, registrar and every consumer read
  and hash recording bytes. This is accepted for correctness and may be
  optimized later (e.g. MCAP summary checks, streaming hashes) without
  changing the contract.
- **Manual fingerprint discipline.** Correct reuse and replace decisions
  depend on producers bumping `semantics_version` (§15.3).
- **Single-recording pipelines.** Building a DatasetVersion from many
  RobotRuns needs many pipeline runs and external orchestration.
- **Zero-unit recording builds fail** in v1 (§18.3).
- **Write-once on object storage is enforced by convention plus
  verification**, not atomically, until conditional writes are adopted.
  Violations are detected, not prevented.
- **Higher storage use from source-preserving materialization.** The ingested
  portion of an external dataset is duplicated, and canonical Scenes keep
  all in-boundary observations rather than a sampled subset (§13.9).
- **Richer canonical manifests.** Per-observation source timing, source
  identity and payload format make manifests larger and their schemas more
  detailed.
- **Larger Scene refactor.** The SceneManifest schema, both Scene producers
  and the detection / scenario readers all change (§13.12, §24 steps 5–7).
- **Possible duplicate derived representations.** Until a shared contract
  such as `AlignedScene` is justified, workflows may each compute similar
  alignments (§13.11).
- **Adapter mapping responsibility.** Each integration owns a correct
  mapping onto canonical semantics and must prove it with mapping tests
  (§20.5).

"Source-faithful" does not mean storing irrelevant SDK internals, caches or
temporary parsing state. It means preserving interpretation-relevant source
information, not byte-for-byte archival (§13.5).

### Neutral

- PostgreSQL / Object Storage / Kafka responsibilities are unchanged
  (ADR-001, -002, -005, -006).
- Integration runtime contract (`IntegrationRequest` / `IntegrationResult`)
  is reused unchanged in shape. LeRobot gains an INGEST operation.

---

## 26. Deferred work

Explicitly outside the initial refactor:

```text
durable CaptureSessionRecord / operational control plane (§11.3)
capture crash / Kafka rebalance recovery
operator realtime UI
discovery/reconciliation of published-but-unregistered RobotRunManifests
artifact garbage collection
multi-recording orchestration (one pipeline run over many RobotRuns)
per-scope build record (to support empty recording-derived scopes)
explicit removal of an external unit from a DatasetVersion
streaming (non-in-memory) recording materialization
conditional-write (If-None-Match) enforcement of write-once keys
reference-only / zero-copy external source mode (§13.9; must not redefine canonical semantics)
payload deduplication / content-addressed payload storage (§27.5)
AlignedScene / ALIGN_SCENE as a shared derived contract (§13.11)
dynamic integration discovery / plugin registry (v1: configuration, §20.5)
generated / simulated / reconstructed Scene ingestion (former SCENE_REGISTRATION, origin types)
Kafka offset/partition provenance in RobotRunManifest (would require v2)
distributed-scale ingestion
Episode learning pipeline redesign
Scenario / Evaluation redesign beyond canonical-boundary migrations
```

Future realtime tracking extends the **control plane** with a separate
operational model. It never reintroduces mutable capture state into
`RobotRunRecord`.

*[ADR-008](./008-acquisition-lifecycle-reliability.md) resolves the
discovery/reconciliation of published-but-unregistered RobotRunManifests and
the classification (not collection) of artifacts. Capture crash and Kafka
rebalance recovery, artifact deletion and the control plane remain deferred.*

---

## 27. Amendment A2: source and provenance contract freeze (step 4)

### 27.1 Scope and transitional state

A2 freezes the shared, domain-neutral source and provenance contracts that
the step-5 `SceneManifest` and step-8 `EpisodeManifest` will compose. They
are implemented as SDK-independent, DB-free `sceneops-core` primitives:

```text
sceneops_core.common.identifiers     open identifier rule; external format,
                                     source clock and producer id validation
sceneops_core.datasets.schemas       ExternalDatasetRef (format validated, strict)
sceneops_core.provenance.sources     ExternalUnitSource · RecordingSegmentSource
                                     UnitSource (union) · ExternalSourceRevision ·
                                     RecordingSourceRevision · SourceRevision (union)
sceneops_core.provenance.source_time SourceTimestampNs · SourceTimeUnit · promote_to_ns
sceneops_core.provenance.producer    ProducerInfo · normalize_build_config ·
                                     compute_producer_fingerprint
```

**Transitional state (CURRENT IMPLEMENTATION).** No Scene or Episode
manifest, record, job or workflow consumes these primitives yet.
`SceneLineage` and `EpisodeLineage` remain the legacy, untyped lineage of
the current manifests until steps 5 and 8 replace them. This is not two
paths for one capability (§23 rule 5): the primitives are contracts with no
runtime producer or consumer. The only runtime-visible change in step 4 is
that `ExternalDatasetRef` now validates `format` and rejects unknown fields.

**Moved from step 4 to step 5.** Three step-4 items in §24 describe
observation- or manifest-storage-level structure, whose concrete shape step
5 owns:

```text
checksum-qualified manifest key helpers      the §19 key rule itself stands
source channel identity primitive            §13.8 semantics stand
payload format / encoding primitive          §27.5 ownership rule stands
```

Defining them before the concrete Scene observation model would design that
model in advance. A2 introduces no observation, frame or payload-collection
type (§20.6).

### 27.2 Source provenance

Every canonical unit has exactly one source block, discriminated by
`source_kind`. The union (`UnitSource`) is provenance only. It does not make
Scene and Episode one type, and neither block carries Scene-, Episode- or
format-specific fields. Both reject unknown fields and are immutable.

**`ExternalDatasetRef`** (KEEP, EXTEND). The existing type already satisfies
the requirements: serializable, SDK-independent, no DatasetVersion
ownership, no Scene/Episode semantics, one shape for import and export.
A2 adds only validation:

```text
format              canonical open identifier (§27.6); validated, never normalized
format_version      that format's own version; non-empty
uri                 location; non-empty; an integration-side locator only (A3):
                    never identity, never canonical provenance
external_name       display only; never identity, never canonical provenance (A3)
external_revision   source revision, where the source has one
checksum            source content checksum, where available
unknown fields      rejected
```

**`ExternalUnitSource`** (refined by A3, §28): `source_kind = "external"`,
`revision` (the `ExternalSourceRevision` below), `source_unit_key`. It
carries no location and no display name; an integration builds it from its
`ExternalDatasetRef` with `ExternalUnitSource.from_ref`, which drops `uri`
and `external_name`.
`source_unit_key` is the source's own stable unit
identity, kept verbatim (open alphabet; non-empty, at most 256 characters,
no surrounding whitespace, no control characters). Source-specific needs
never add fields here; genuinely new domain semantics amend the domain
contract instead (§20.3).

**`RecordingSegmentSource`**: `source_kind = "recording"`, `robot_run_id`,
`recording_artifact_id`, `recording_checksum`, `source_clock`,
`start_timestamp_ns`, `end_timestamp_ns`, `unit_key`.

```text
robot_run_id            source identity authority (RobotRunRecord)
recording_artifact_id   must equal robot_run_recording_artifact_id(robot_run_id)
recording_checksum      "sha256:<64 lowercase hex>"; pins the exact bytes
source_clock            copied from the RobotRun; never defaulted
                        (amended by A5, §30.2: the segmentation clock the
                        producer declares)
window                  [start_timestamp_ns, end_timestamp_ns), half-open, non-empty,
                        integer ns in source_clock
unit_key                producer-defined stable key of the unit within the recording;
                        derived from source + build configuration only
excluded                recording URI, DatasetVersion, job / pipeline ids,
                        execution timestamps, Scene/Episode fields
```

`unit_key` is kept from §14.1, so the registrar can derive recording unit
identity (§18.1) from the source block alone, in the same way it uses
`(format, source_unit_key)` for external units.

**Source revision.** Each block projects the build-level identity of the
exact source bytes it read, `source_revision()`:

```text
ExternalSourceRevision    format, format_version, external_revision, checksum
RecordingSourceRevision   robot_run_id, recording_checksum
```

The unit key, window, `uri` and `external_name` are excluded. This revision
is the fingerprint's source input (§27.7), so a manifest's fingerprint can
be re-derived from the manifest alone.

**Revision strength (known limitation).** If an integration supplies
neither `external_revision` nor `checksum`, an external source revision is
only `(format, format_version)`. Two different contents under the same
version string then fingerprint equal, and §18.2 falls back to the manifest
checksum comparison to detect the change. Integrations set
`external_revision` whenever their source defines one.

### 27.3 Source time precision and clock semantics

Decision: **canonical source time is an integer count of nanoseconds in a
declared source clock.**

```text
1. Source timestamp identity is an integer in its declared clock domain.
2. Nanosecond sources (ROS2, MCAP) remain nanosecond-exact.
3. Lower-precision sources are promoted exactly by integer multiplication
   (promote_to_ns: s / ms / us → ns). Floats are refused, not rounded.
4. No floating-point value participates in source-time identity or fingerprints.
5. No UTC interpretation is forced onto a clock that is not a wall clock.
6. Rendering a datetime is presentation, unless the clock is itself defined
   as UTC / wall clock.
```

Field names carry the unit: `timestamp_ns`, `start_timestamp_ns`,
`end_timestamp_ns`. Values are bounded to `[0, 2^63 − 1]` so they round-trip
unchanged through PostgreSQL `BIGINT`, Parquet `INT64` and Arrow
`timestamp[ns]`.

**Clock semantics.** A source timestamp is not interpretable without its
clock domain; `(timestamp_ns, source_clock)` together define temporal
meaning. The shared representation is the smallest existing one: the open
`source_clock` string that `RobotRunManifest.capture.source_clock` and
`RobotRunRecord.source_clock` already carry, now with the canonical
identifier rule (§27.6). No universal time system is introduced.

```text
SceneOps-defined clocks       unqualified       mcap_log_time (MCAP Message.log_time)
dataset-defined timebases     <format>.<clock>  e.g. "nuscenes.<clock>"; the
                                                 integration names and documents it
```

The clock identifier defines the semantics: sensor clock, log time,
robot/system clock, UTC wall clock or a dataset timebase. Whether a clock is
a wall clock is a property of its definition, never an assumption made by a
consumer. Where a manifest declares the clock (per unit, per channel or per
observation) is decided by steps 5 and 8, under one rule: every canonical
source timestamp has exactly one declared clock reachable from the
manifest.

**Relation to `RobotRunManifest` v1** (unchanged). Its `started_at` /
`ended_at` are microsecond, UTC-rendered summaries of `mcap_log_time`,
truncated toward negative infinity (§8.3), and they rely on MCAP log time
being Unix-epoch based. They are search and display projections, not
source-time identity. Segment windows are derived from the recording bytes
in nanoseconds. They are never derived from those fields, and never
validated against them, since truncation can place a true nanosecond
message time after `ended_at`. Supporting a recording clock that is not
epoch-based would require `RobotRunManifest` v2.

### 27.4 Channel boundary and the nuScenes v1 Scene boundary

> **Amended by A4.** The channel-boundary rule stands. The "nuScenes (v1
> interpretation)" paragraph is superseded. nuScenes is no longer a
> canonical source, so its keyframe/sweep boundary is the external tool's
> concern: the tool records every sweep it converts (§29.13). Annotations
> and keyframe groupings are open question Q1 (§29.15).

A2 confirms §13.5–§13.6 as a frozen rule:

```text
exclude an entire configured source channel      canonical boundary decision
keep a channel but drop some of its observations derived (lossy sampling)
keep only the frame nearest another sensor       derived (association)
discard sweeps a downstream model does not need  derived
```

A channel selection is explicit, carried in `ProducerInfo.build_config` as
a sorted list, and therefore part of the fingerprint. Source absence or
corruption inside the boundary stays explicit where the source exposes it;
an observation is never silently treated as if it did not exist.

**nuScenes (v1 interpretation).** A canonical nuScenes Scene covers the
selected source scene and the configured source channels, and preserves
**all** available observations (keyframes and sweeps) of those channels
within the scene. Keyframe membership is source semantics and may be
marked. Annotations stay attached as the source defines them (keyframes
only) and are never synthesized for sweeps. Keyframe-only selection for
detection is a derived transformation. The migration happens in step 6
(External Scene ingestion normalization), after step 5 defines the
concrete `SceneManifest`.

### 27.5 Payload ownership

Decision: **selected canonical observation payloads are SceneOps-owned,
immutable, checksummed artifacts for both source kinds.**

```text
RobotRun recording artifact ── provenance / original archive (retained)
        ↓ resolve_recording()
domain construction
        ↓
selected observation payload extraction
        ↓
SceneOps-owned canonical payload artifacts
        ↓
SceneManifest / EpisodeManifest references
```

External sources follow the same rule (§13.9): integration → SceneOps-owned
artifacts → canonical manifest. Normal downstream payload access never
needs an external dataset directory, an external SDK, an external root
path, or MCAP parsing. The original source identity stays in the unit's
source block.

Accepted costs: extra storage, and payload extraction during
canonicalization. Benefits: one payload access path for every source,
independent payload verification, and source-format-independent downstream
contracts. Deduplication and content-addressed payload storage are DEFERRED
(§26), as is the reference-only external mode (§13.9). Step 4 implements no
extraction; steps 5 and 7 define and implement the Scene payload
representation, and step 8 the Episode one.

### 27.6 External format identifiers and integration resolution

> **Amended by A4 (§29.9).** The identifier rule stands for `source_clock`,
> `producer_id`, and integration-runtime format names. External format
> identifiers no longer enter canonical identity, records or fingerprints.
> `(domain, format) → INGEST runtime` resolution is superseded. Resolution
> remains only for interoperability (EXPORT) runtimes.

**Identifier rule.** External format identifiers are open, stable strings:

```text
lowercase ASCII, [a-z0-9][a-z0-9._-]*, at most 64 characters
e.g. nuscenes · lerobot · waymo-open-dataset
```

The stored identifier must already be canonical. It is validated and never
lowercased or rewritten, because it enters unit identity (§18.1), the
`external_format` projection (§13.3) and the fingerprint. Aliases for user
input ("NuScenes") are resolved at the integration boundary before a value
enters a contract. Renaming a canonical identifier is an identity-affecting
migration, not a cosmetic edit. Format revision (`format_version`,
`external_revision`, `checksum`) stays separate from format identity. The
same rule applies to `source_clock` (§27.3) and to `producer_id` (up to 128
characters). SceneOps-owned closed sets versioned by `schema_version`
(`RobotRunManifest.recording.format`, `capture.source.kind`) are not
external identifiers and are unaffected (§20.4).

**Integration resolution.**

```text
SceneOps core                   open identifier strings + serialized contracts
                                (ExternalDatasetRef, IntegrationRequest /
                                 IntegrationResult, canonical manifests)
integration / application layer resolves (domain, format) → integration runtime
                                ("scene", "nuscenes")  → nuScenes runtime
                                ("episode", "lerobot") → LeRobot runtime
```

In v1 the mapping is static registration or configuration in the
application/integration layer. It is never a closed core enum. Dynamic
plugin discovery is DEFERRED (§26). Step 4 freezes the ownership boundary
only; the worker's current dispatch is migrated in steps 6 and 9 (§27.9).

### 27.7 ProducerInfo, build configuration and the producer fingerprint

**`ProducerInfo`**: `producer_id`, `semantics_version`, `build_config`,
`producer_fingerprint`. It describes how the unit was constructed, never
the execution that ran it. Unknown fields are rejected. `create(...)`
computes the fingerprint; `verify(source_revision)` re-derives it from a
parsed manifest's own source block.

**`build_config`** contains only configuration that affects the semantics
or the selected boundary of the produced unit:

```text
include   selected channels · segmentation policy · source-unit selection rule ·
          domain-defining thresholds · explicit boundary configuration ·
          limits that truncate the produced unit set (§15.2)
exclude   temp directories · worker count · logging · job / pipeline ids ·
          retry count · storage endpoints / output roots · execution timestamps
```

Normalization is split. The core does mechanical normalization: a JSON
object of plain JSON values with string keys and finite floats only,
round-tripped through the canonical serializer (§8.3). Sets are rejected
because their iteration order is not deterministic. Well-known
execution-scoped keys (`job_id`, `pipeline_run_id`, `pipeline_task_run_id`,
`execution_id`, `generated_at`, `created_at`, `hostname`, `worker_hostname`)
are rejected at any depth. That deny-list is a guard against the likeliest
mistake, not a substitute for producer discipline. Semantic normalization
is part of each producer's contract: defaults made explicit, semantically
unordered collections sorted (selected channels as a sorted list), and
fields without output effect omitted. The core cannot know which lists are
unordered, so list order is significant to the fingerprint.

**Fingerprint (exact definition).**

```text
producer_fingerprint = "sha256:" + hex(sha256(canonical_json_bytes({
    "fingerprint_schema": "sceneops.producer_fingerprint/v1",
    "producer_id":        producer_id,
    "semantics_version":  semantics_version,
    "build_config":       normalize_build_config(build_config),
    "source":             source_revision          (§27.2; nulls emitted)
})))
```

```text
same source revision + same producer_id + same semantics_version
  + same normalized build_config          → same fingerprint
any of them different                     → different fingerprint
```

`fingerprint_schema` makes any future change to this definition explicit
rather than silent. The source revision is build-scoped (no unit key, no
window), which matches §18: one fingerprint per recording scope (§18.3),
and one per external ingestion compared per unit together with the manifest
checksum (§18.2). The fingerprint is computed from inputs before building.
The output manifest checksum is never an input, because that would be
circular. The §15.3 discipline is unchanged: if output semantics change for
unchanged source and configuration, `semantics_version` must be bumped.

### 27.8 Fingerprint, manifest checksum and manifest revision

```text
producer_fingerprint   semantic equivalence of construction      (inputs)
manifest checksum      identity of the exact serialized bytes     (output)
manifest_artifact_id   the exact registered manifest revision     (record pointer)
```

These are never collapsed. Two builds with equal fingerprints are
semantically equivalent even if a non-semantic detail differs. Equal
checksums mean byte-identical manifests. A record's current revision is
exactly its `manifest_artifact_id`, never "latest" (§14.4). Step 4 does not
migrate `SceneRecord` / `EpisodeRecord`; steps 5 and 8 do.

### 27.9 CURRENT IMPLEMENTATION that conflicts with A2 (deferred)

| CURRENT (HEAD ce56f8e) | Conflict | Resolved in |
|---|---|---|
| `timestamp_us` on `SceneSensorFrameManifest`, `SceneSampleManifest`, `SceneAnnotationManifest`, Episode frames, `EpisodeManifest.start/end_timestamp_us`, `RobotStateRecord` | microsecond, no declared clock (§27.3) | steps 5 / 8 (`RobotStateRecord` with the robot-state consumers, step 8 or 10) |
| `SceneLineage`, `EpisodeLineage` (`raw_log_id`, `source_dataset_*`, free-form `metadata`) | untyped provenance; no source revision or producer | steps 5 / 8 compose `UnitSource` + `ProducerInfo` |
| `SceneGenerationMetadata` (`generator_name`, `generator_version`, `params`) | producer identity without fingerprint | step 5 |
| `MCAP_LOG_TIME_CLOCK` defined in `episodes/alignment/config.py` and imported by the Recording Publisher | shared clock identifier lives in an Episode module | step 7 or 8 (move to a shared location when a second consumer appears) |
| nuScenes integration: keyframe `sample_data` only; frame `uri` relative to the dataroot | §27.4, §27.5 | step 6 |
| `IngestScenesJobParams.source_format: DatasetType` + `if … == DatasetType.NUSCENES` dispatch; `NUSCENES_FORMAT` constant in the worker | closed-enum dispatch instead of `(domain, format)` resolution (§27.6) | step 6 |
| LeRobot runtime: EXPORT-only, `SUPPORTED_FORMAT = "lerobot"` | no INGEST registration | step 9 |
| `RobotRunManifest` v1 µs UTC `started_at` / `ended_at` | not source-time identity (§27.3) | no change; v2 only if a non-epoch clock is needed |
| `ArtifactRef` (`metadata` free-form, extra fields ignored) | not a strict payload reference | step 5, if Scene payload references need a strict primitive |

---

## 28. Amendment A3: locator-free canonical source provenance (step 5)

> **Made moot by A4 (§29.9).** `ExternalUnitSource` is removed, so no
> canonical provenance block can carry a locator or display name. I-30's
> rule still holds and now holds structurally: `RecordingSegmentSource`
> carries none. `ExternalDatasetRef` remains an integration-side locator only.

**Problem.** `ExternalDatasetRef.uri` and `external_name` were excluded from
unit identity (§18.1) and from the producer fingerprint (§15.2), but A2's
`ExternalUnitSource` embedded the whole `ExternalDatasetRef`. Every canonical
manifest therefore serialized the dataset location and display name, so the
same dataset revision mounted at `/data/raw/nuscenes` and at
`s3://mirror/nuscenes`, or labelled differently, produced different manifest
bytes and checksums from semantically identical builds.

**Decision.** Location and display names may help an integration or a
person find a source; they never affect canonical manifest identity.

```text
integration side (before canonicalization)
  ExternalDatasetRef   format, format_version, uri, external_name?,
                       external_revision?, checksum?
        │  ExternalUnitSource.from_ref(ref, source_unit_key=…)
        │  (uri and external_name dropped)
        ▼
canonical provenance (in every external canonical manifest)
  ExternalUnitSource   source_kind = "external"
                       revision         ExternalSourceRevision
                                        (format, format_version,
                                         external_revision?, checksum?)
                       source_unit_key
```

- `ExternalSourceRevision` is the A2 type, reused unchanged. It remains the
  fingerprint's source input, so `producer_fingerprint` values are unchanged.
- Unit identity is unchanged: `revision.format` + `source_unit_key` (§18.1).
- `RecordingSegmentSource` is unchanged; it never carried a location or a
  display name.
- `ExternalDatasetRef` is unchanged and keeps `uri` and `external_name` for
  integration requests, results and display (§20).

**Consequences.** Canonical consumers have no path to an external dataset
location, and moving, re-mounting or renaming a source does not create a
new manifest revision. Canonical provenance does not record where or under
what name an integration read the source; that is execution history, which
belongs to the integration run and its job lineage, not to the canonical
unit. No canonical external manifest had been registered before A3, so no
stored manifest or record needs migration.

---

## 29. Amendment A4: acquisition-first architecture

### 29.1 Context

SceneOps is a **robot data platform**. In production, source data comes
from real-world acquisition. Converting existing external datasets is not
the primary flow:

```text
real-world acquisition → raw durable recording → RobotRun
    → batch canonicalization → Scene / Episode
    → validation · profiling · curation · inference · evaluation
```

ADR-007 as amended through A3 also froze a second canonical ingress:
`external dataset → integration → SceneManifest / EpisodeManifest`. A4
removes it.

**Audit (CURRENT IMPLEMENTATION, HEAD 1406cb2).**

```text
Acquisition (L0 → L1)
  ros2/nodes/can_replay_node.py     nuScenes CAN → 5 ROS2 telemetry topics; source time in
                                    header.stamp or a JSON field; /mission/status is synthetic
                                    and carries replay wall-clock time
  ros2/nodes/streaming_bridge_node  ROS2 → TelemetryEnvelope → Kafka (key = robot_run_id)
  ros2/capture/                     Kafka → per-run sequence check (exact redelivery dropped,
                                    conflict/gap fails) → rosbag2 MCAP, .partial → fsync → rename
                                    log_time = source_timestamp_ns · publish_time = ingest time
                                    (log_time conflicts with §29.5 R4)
                                    payload bytes passed through unchanged
                                    static channel registry: 5 telemetry topics; no camera,
                                    lidar, tf or CameraInfo
  sceneops_integrations.recording   DB-free Recording Publisher: MCAP facts (one channel
                                    definition per topic), write-once upload, RobotRunManifest
                                    last; invoked by CLI, no automatic capture → publish hand-off
  REGISTER_ROBOT_RUN                verify + project → immutable RobotRunRecord
  batch path                        the publisher accepts any finalized local MCAP
                                    (capture.source.kind = file | ros2_bag | kafka); nothing
                                    produces a sensor-bearing MCAP

Canonicalization (L1 → L2)
  Scene    SceneManifest v1 + REGISTER_SCENES (step 5) accept ExternalUnitSource |
           RecordingSegmentSource. No producer emits a canonical SceneManifest:
           INGEST_SCENES (nuScenes integration, mode=scene_manifest) and BUILD_SCENES
           (raw log, default NUSCENES_RAW_LOG_MOCK) emit LEGACY_SCENE_MANIFEST only.
           No OBSERVATION_PAYLOAD producer exists. No Scene is registered.
  Episode  BUILD_EPISODES consumes resolve_recording(robot_run_id), decodes the MCAP with
           RosbagAdapter (topic defaults; steering/throttle/brake hard-wired), and emits
           the legacy EpisodeManifest.

Derived (L2 → L3)
  detection       keyframe groups → samples (scenes/keyframes.py); ground truth only from
                  nuScenes annotations
  robot states    INGEST_ROBOT_STATES / missions: telemetry projections of the resolved recording
  interop         LeRobot EXPORT (isolated runtime); no INGEST path
```

Findings:

1. Steps 1–3 already made the recording the platform's only *verified*
   ingress. A RobotRun exists only if its recording was published and
   verified, and Episodes are already built only from it.
2. The external Scene path has never registered output. Every
   external-specific element of steps 4 and 5 is a contract with no
   producer (§29.9).
3. `can_replay_node.py` is already an external-dataset → streaming
   acquisition simulator. A4 generalizes that role and does not invent it.
4. `RosbagAdapter` maps ROS topics onto nuScenes channel names
   (`/camera/front/image → CAM_FRONT`). That is the recording path imitating
   one external format's vocabulary, which §13.8 forbids.
5. `/mission/status` carries replay wall-clock time, so two acquisitions of
   the same source produce different recordings (§29.12).
6. Capture writes the envelope's source timestamp into MCAP `log_time`.
   §29.5 reserves `log_time` for the recorder's receive time. The source
   timestamp is still preserved in every v1 payload (`Header.stamp` or the
   JSON `source_timestamp_ns` field), so the fix loses no information
   (§29.20).

**Why direct external → canonical ingestion is superseded.**

- *It tests a path production never uses.* The production path, recording
  → Scene, had no sensor-bearing input at all. Converting external datasets
  into recordings makes them exercise exactly the production path.
- *Two producers per domain means two mappings to keep equivalent.* Every
  external format needed its own mapping onto SceneManifest/EpisodeManifest
  and its own canonical rules: external unit boundaries (§27.4), per-unit
  replacement (§18.2), external identity, locator handling (§28). The
  recording builder must exist anyway. One builder per domain gives one
  place where canonical semantics are decided.
- *Format knowledge leaks into core.* External format identifiers entered
  unit identity, record projections and fingerprints (§13.3, §18.1, §27.6).
  With A4 the core holds no external format identifier in any canonical
  structure.
- *Source independence becomes structural.* §20.1 asked downstream code not
  to branch on source format. With one source kind, there is nothing to
  branch on.

Costs are in §29.22.

### 29.2 Decision

```text
1. Raw acquisition / RobotRun is the primary — and only — platform ingress for
   acquired sensor and robot-state data. Lineage roots that are not acquired,
   such as labels, are outside this statement (§29.3, §29.15).
2. Streaming and batch acquisition converge on the same immutable L1 recording
   + RobotRun contract (§29.4, §29.5).
3. Scene and Episode are produced only from registered raw recordings, through
   batch canonicalization (RECORDING_SCENE_BUILDING, RECORDING_EPISODE_BUILDING).
4. External datasets primarily serve acquisition simulation and testing.
5. External dataset adapters are not canonical Scene/Episode producers. They
   live in a standalone tool that emits ROS2 messages or a local MCAP, and
   nothing else (§29.13).
6. Source format may be retained as acquisition-origin information for
   inspection and debugging (§29.8). It is never a processing boundary.
7. Canonicalization is source-format independent at the platform level. Its
   behavior is a function of recording content, producer semantics and build
   configuration only (I-32).
8. Synchronization, sampling, association and fusion remain derived (§13.6,
   unchanged).
9. MCAP (ROS 2 profile) is the concrete L1 recording format for v1 (§29.6).
10. RobotRun is L1 acquisition provenance. It is never DatasetVersion membership
    and never a domain unit (§29.7, unchanged from §10).
```

Pipeline taxonomy after A4:

```text
REGISTER_ROBOT_RUN            (standalone)  L1 ingress, both acquisition modes
RECORDING_SCENE_BUILDING      robot_run_id ─▶ BUILD_RECORDING_SCENES   ─▶ REGISTER_SCENES
RECORDING_EPISODE_BUILDING    robot_run_id ─▶ BUILD_RECORDING_EPISODES ─▶ REGISTER_EPISODES
```

### 29.3 Data layers

```text
L0  Live transport            ROS2 topics · Kafka (TelemetryEnvelope) · sensor streams
                              transient; bounded replay only (ADR-005); never canonical

L1  Immutable raw acquisition recording (MCAP, write-once artifact) + RobotRunManifest
                              + RobotRunRecord
                              source-faithful and lossless; holds no domain decision;
                              the earliest durable layer and the root of acquired
                              sensor / robot-state lineage

L2  Canonical domain data     SceneManifest / SceneRecord · EpisodeManifest / EpisodeRecord
                              · OBSERVATION_PAYLOAD artifacts · DatasetVersion membership

L3  Derived workflow data     keyframe / sample views · AlignedEpisode · ScenarioSet
                              · inference · evaluation · learning exports · LeRobot export
                              · dataset index · telemetry projections (robot_states, missions)
```

Transitions:

```text
L0 → L1   streaming acquisition: capture → finalize → publish → REGISTER_ROBOT_RUN
(none → L1) batch acquisition: complete recording → publish → REGISTER_ROBOT_RUN
L1 → L2   batch canonicalization: resolve_recording → recording builder → registrar
L2 → L3   derived workflows, pinned to manifest revisions (§18.5)
L1 → L3   telemetry projections read the resolved recording directly; they are
          derived and never canonical
```

No layer writes into an earlier one. Acquired content in L2 and L3 can be
regenerated from L1. L1 cannot be regenerated from L0, so it is the
durability-critical layer for acquired data.

L1 is the root of *acquired* sensor and robot-state lineage, not the only
possible lineage root. Canonical units may later gain further roots that are
not acquisition: human labels, pseudo labels, external ground-truth imports
and other derived annotations. Their architecture is open (Q1, §29.15). This
amendment only makes sure L1 does not rule them out.

Relation to §4: layers A–E are *dependency* layers and are unchanged.
L0–L3 are *data-maturity* layers:

```text
L0 = layer A runtime (bridge, Kafka, capture)
L1 = RobotRunManifest (B) + recording (C) + RobotRunRecord (D)
L2 = Scene/Episode manifests (B) + payloads (C) + records and membership (D)
L3 = layer E
```

The external acquisition tool sits outside all layers (§29.13).

### 29.4 Acquisition modes

**Streaming acquisition.** It reliably captures continuously produced data
and is not required to build Scenes or Episodes.

```text
robot / sensors (or tool replay, §29.13)
  → ROS2 topics → streaming_bridge_node → Kafka (key = robot_run_id)
  → capture (CaptureSession, sequence integrity, .partial → finalize)
  → finalized local MCAP → Recording Publisher → MCAP + RobotRunManifest
  → REGISTER_ROBOT_RUN → RobotRunRecord
```

**Batch acquisition.** A complete recording exists before SceneOps sees it.
It skips live transport and capture, and joins the same boundary:

```text
complete L1-conformant recording (§29.5)
  → Recording Publisher (capture.source.kind = file) → MCAP + RobotRunManifest
  → REGISTER_ROBOT_RUN → RobotRunRecord
```

The input to batch acquisition is a *recording*, not a dataset. In v1 a
batch input is any MCAP that meets §29.5. Possible producers are the
external acquisition tool, a robot's onboard recorder that writes the
contract, or capture run offline. A standard rosbag2 MCAP recording already
writes the recorder's receive time into `log_time`, as R4 requires. It
conforms when it meets the rest of §29.5. A recording that lacks required
channels (for example R9 calibration) is a writer or tool concern.

**Convergence.** Both modes end at the same publisher, the same
`RobotRunManifest` v1, the same `REGISTER_ROBOT_RUN` and the same
`resolve_recording`. From RobotRun downward, recordings from a real robot, a
replayed dataset and a batch-converted dataset take one code path.
`capture.source.kind` records the mode for inspection only (I-32).

### 29.5 L1 raw-recording contract

> **An L1 recording v1 is one MCAP file (write-once artifact) plus one
> `RobotRunManifest` v1, registered as one RobotRun.** It preserves
> acquired messages losslessly. It holds no downstream decision.

| # | Requirement | Carried by | HEAD 1406cb2 |
|---|---|---|---|
| R1 | Channel identity | MCAP `Channel.topic`, one channel definition per topic; `RobotRunManifest.channels[].topic` | holds (publisher rejects a topic with two definitions) |
| R2 | Payload bytes exactly as produced; no transcoding at acquisition | MCAP `Message.data` | holds (capture passes bytes through, verified) |
| R3 | Schema and encoding, self-describing | MCAP `Schema` (name, encoding, data) + `Channel.message_encoding`; names repeated in the manifest | holds |
| R4 | Timing facts preserved, each with its own meaning | `Message.log_time` = recorder / capture receive time, integer ns. `Message.publish_time` = upstream publication time when the transport provides one, otherwise equal to `log_time`. **Source observation time** = the timestamp the source message or schema carries, where one exists (e.g. ROS `Header.stamp`, a declared payload field). It stays in the payload (R2), unrewritten, in whatever clock the source defines, with no epoch requirement. Acquisition never substitutes one of these times for another. A timing fact that is available only at transport level must be preserved in the recording too | publish_time holds (capture writes bridge ingest time). **Conflict:** capture writes the envelope source timestamp into `log_time` (§29.20). Source timestamps are preserved in every v1 payload |
| R5 | Clock semantics | `capture.source_clock = "mcap_log_time"` names the **recording clock**: the clock `log_time` is written in. A live recorder writes wall-clock receive time, which is Unix-epoch based, as `started_at` / `ended_at` require (§27.3). The name `source_clock` is historical, and v1's schema is unchanged. It makes no claim about any source message's observation time | holds (publisher supports only `mcap_log_time`); meaning clarified here |
| R6 | Ordering evidence | Capture and file order are acquisition evidence. They are preserved where the format provides them: MCAP write order, which for capture is the order consumed from the run's Kafka partition. Source or transport sequence information is preserved when available (e.g. MCAP `Message.sequence`, the envelope `sequence_number`). Canonical temporal ordering is never inferred solely from physical file order (I-34). A transport redelivery (same transport sequence and payload) is not an acquired message and is not recorded. Source-level duplicates are recorded as separate occurrences | capture preserves consumed order, drops exact redeliveries, and fails on gaps or conflicts. Envelope `sequence_number` is validated but not written to the recording (§29.20) |
| R7 | Robot identity | `run_id`, `robot_id`, optional `robot_platform` (§8, §9) | holds |
| R8 | Recording extent | `started_at` / `ended_at` (µs projections, §27.3); exact ns bounds derived from the bytes | holds |
| R9 | Calibration and transforms | Ordinary channels with standard messages (e.g. `tf2_msgs/msg/TFMessage` on `/tf_static` and `/tf`, `sensor_msgs/msg/CameraInfo`). Static or latched data must be recorded at least once, at or before the first observation that depends on it | **gap**: no writer records them |
| R10 | Robot state and telemetry | Ordinary channels | holds |
| R11 | Events recorded at acquisition time (mission/task markers, interventions) | Ordinary channels whose source-semantic timestamps are on the source timeline. A synthetic event must not carry replay wall-clock or replay-pacing time as its source timestamp | **violated** by `/mission/status` (§29.20) |
| R12 | Acquisition provenance | Optional, non-semantic MCAP metadata record (§29.8) | not written |

**Excluded from L1.** Scene/Episode boundaries and unit keys. Channel →
modality / sensor / frame-role mapping. Sampling, synchronization and
association decisions. DatasetVersion references. Labels produced after
acquisition (§29.15). Kafka partitions and offsets (§8.2, unchanged).

**Encoding profile.** SceneOps-produced recordings use the MCAP ROS 2
profile (`message_encoding = cdr`, `schema_encoding = ros2msg`), which is
what capture already writes. L1 does not reject other encodings, because a
recording is a lossless archive. Canonicalization supports only the
decoders it declares. Selecting a channel whose encoding is unsupported
fails the build loudly. It is never skipped.

**Canonical observation time is a canonicalization decision.** For each
channel, the producer's `build_config` designates which preserved timestamp
is the canonical observation time, and declares that time's clock
identifier (§27.3, `SceneChannel.source_clock`). The choices are a
source-message field such as `Header.stamp`, `publish_time`, or `log_time`.
Acquisition makes no such choice. Under A2, unchanged here, a
`RecordingSegmentSource` window is in the RobotRun's clock, which is now
the recording clock. Whether segmentation should instead use a
source-semantic clock is open question Q4 (§29.21).

**Batch writers.** A batch writer has no live reception, so it acts as the
recorder. It sets `log_time` to a deterministic simulated receive time
derived from the source (normally the source timestamp, where that is
epoch-based), and sets `publish_time = log_time`. It never uses
conversion-time wall clock, so its output is reproducible.

**Verification.** R1–R3, R7 and R8 are checked from the bytes by the
publisher and by registration (§7.2, §12.1). R4, R6, R9 and R11 are *writer
obligations* that the bytes cannot fully prove. Writer tests check them against
source data, as `ros2_streaming_verify.py` already does for capture. Every
L1 writer (capture and the external tool) must pass one shared conformance
suite (§29.19, step 6).

**No `RobotRunManifest` change.** v1 already carries every fact the
contract needs at the manifest level. Everything else lives in the
recording bytes, which the manifest pins by checksum.

### 29.6 MCAP's role

**Decision: option A.** MCAP (ROS 2 profile) is the concrete L1 recording
format for SceneOps v1. There is no format-independent `RawRecording`
abstraction.

- MCAP already provides what the contract needs: topic identity, embedded
  schemas, explicit encodings, ns timestamps, metadata records and
  attachments.
- Capture already writes MCAP through rosbag2's MCAP storage plugin, so
  "rosbag2" in v1 *is* MCAP. rosbag2's sqlite3 storage is not needed.
- No second format exists or is planned. An abstraction now would be
  designed without a second use case (architecture rule 5).
- The extension point already exists. `RobotRunManifest.recording.format`
  is a closed set (`{"mcap"}`) versioned by `schema_version` (§8.4, §20.4).
  A future format adds a value and a reader. It adds no layer.

Implementation hygiene, not a platform abstraction: recording builders and
telemetry projections read through one internal reader that turns an MCAP
into a stream of messages, each with its topic, schema, payload, and
preserved timing and ordering facts (`log_time`, `publish_time`, `sequence`
where present, acquisition order). Canonical manifests never depend on
MCAP physical structure (channel ids, chunk offsets, file position). The
timestamps they carry come from preserved timing facts, as `build_config`
selects (§29.5). Nothing nuScenes-specific can reach canonicalization,
because builders see only ROS messages.

### 29.7 RobotRun's final role

```text
RobotRun is              the L1 acquisition and provenance unit: one acquisition,
                         one recording, one RobotRunManifest, one immutable
                         RobotRunRecord (§10, unchanged)
                         the source-identity authority for every canonical unit
                         (RecordingSegmentSource.robot_run_id)
                         the scope of recording-derived replacement (§18.3)
                         the input of every recording builder and telemetry projection

RobotRun is not          a DatasetVersion member · a Scene · an Episode
                         · a synchronized sample set · a model-ready representation
                         · a lifecycle object (CaptureSession owns runtime state, §11)

One RobotRun may yield   many Scenes and many Episodes, in many DatasetVersions,
                         under different producer configurations; each
                         (DatasetVersion, domain, robot_run_id) scope has one
                         current producer fingerprint (§18.3)
```

### 29.8 Acquisition-origin provenance

Facts like "this recording was produced from nuScenes v1.0-mini /
scene-0061" are useful for debugging and lineage. They are never dispatch
inputs.

**Decision.** Origin information lives *inside the recording*, as an
optional MCAP metadata record named `sceneops.acquisition_origin`. Its keys
and values are defined by the writer, for example `tool`, `tool_version`,
`source_format`, `source_version` and `source_unit`.

- It is covered by the recording checksum, immutable, readable with
  standard MCAP tools, and needs no schema change.
- It is **not** a `RobotRunManifest` v1 field. §8.2 excludes free-form
  metadata, and a typed origin field would invite dispatch. If search by
  origin becomes a real need, a v2 may add an optional display-only origin
  descriptor (DEFERRED).
- It is **not** `ArtifactRecord` metadata. That record describes integrity
  and execution, not source facts.
- It is **not** on `SceneRecord`, `SceneManifest` or
  `RecordingSegmentSource`. Canonical provenance is `robot_run_id` plus
  the recording checksum, and origin is reachable through that recording.

Rules: canonicalization never reads origin metadata, `capture.source.kind`
or `robot_platform` to choose behavior (I-32). Origin metadata is not part
of identity or the fingerprint. It is pinned only indirectly, through the
recording checksum: different origin bytes are a different recording
revision, as expected. It is excluded from batch/streaming equivalence
(§29.12). A source location is never canonical identity. Run-id naming for
test data (e.g. `nuscenes-v1-0-mini-scene-0061`) is a tool convention, and
the platform never parses it.

### 29.9 Canonical source provenance after A4

**`ExternalUnitSource`: option A, removed from canonical provenance.**
The removal is implemented in step 7 (§29.19), not here.

- No canonical external manifest was ever registered, and no producer emits
  one (§29.1). Removing it migrates no stored data.
- Keeping it dormant (option B) would leave a canonical branch with no
  producer and no test that exercises real data. Every consumer would carry
  it: the registrar's per-unit rules, record CHECK constraints, identity,
  the API filter, analytics columns and keyframe helpers. It would also be
  a second path for one capability (§23, rule 5).
- Option C (moving it to an interop package) is option A under another
  name. Provenance outside the canonical manifest schema is not canonical
  provenance.
- If external canonical ingest is ever needed again, a new amendment
  readmits it, with a concrete use case that acquisition cannot serve.

The serialized discriminators stay: `RecordingSegmentSource.source_kind =
"recording"`, `RecordingSourceRevision.source_kind = "recording"`, and the
`source_kind` member of the unit-id identity document. So the bytes of
recording-derived manifests, the `fingerprint_schema` and `unit_id_schema`
definitions, and recording unit ids are all unchanged.

**`ExternalDatasetRef`: integration-runtime locator only.** It stays in
`sceneops-core` because `IntegrationRequest` and `IntegrationResult` use it
for interoperability runtimes (the LeRobot EXPORT). It moves from
`sceneops_core.datasets` to `sceneops_core.integration_runtime`, and nothing
in provenance, identity, records or fingerprints references it.
`ExternalSourceRevision` is deleted. The external acquisition tool does
**not** use `ExternalDatasetRef`. It has its own input descriptor (dataset
root, version, unit selection) and no dependency on SceneOps packages
(§29.13). The difference:

```text
integration locator     where an integration or tool reads or writes an external
                        dataset; execution input; never persisted as provenance
canonical provenance    RecordingSegmentSource (robot_run_id, recording checksum,
                        window, unit_key) + ProducerInfo
```

**Step 4/5 contracts over-generalized for the superseded external path.**
They are identified here and removed in step 7:

```text
sceneops_core.provenance.sources    ExternalUnitSource, ExternalSourceRevision, and the
                                    UnitSource / SourceRevision unions
sceneops_core.provenance.identity   external branch; UnitSourceProjection.external_format
SceneLineage.source                 union → RecordingSegmentSource
SceneManifest.declared_window()     None branch (every Scene has a declared window)
SceneRecord / scenes table          source_kind, external_format, nullable robot_run_id and
                                    window columns; ck_scenes_source_projection and the
                                    all-or-none ck_scenes_declared_window (migration
                                    7a3d5e9c1b42)
REGISTER_SCENES                     §18.2 per-unit external branch; mixed-source-kind check
scenes API / analytics              external_format filter and column
scenes/keyframes.annotation_source  external branch
sceneops_core.scenes.testing        external_source fixture defaulting to "nuscenes"
ExternalDatasetRef placement        sceneops_core.datasets → integration_runtime
IntegrationOperation.INGEST         no remaining operation
```

Retained as generic schema capabilities, because recordings can populate
them: `SceneManifest.groups`, since a hardware-triggered synchronized
capture set is a source-defined grouping. Also `annotations`, pending Q1
(§29.15).

### 29.10 Recording canonicalization

Both builders run in the worker as the producer task of their pipeline
(§17.3, §17.4, which stand). Both satisfy §13.5–§13.10, §15, §17.5, §18.3
and §27.5.

**`RecordingSceneBuilder`** (`BUILD_RECORDING_SCENES`)

```text
inputs     robot_run_id → resolve_recording() → VerifiedRecording (path, checksum,
           format, source_clock); RobotRunRecord facts
           build_config (part of the fingerprint):
             included topics                       boundary decision (§13.5)
             topic → modality · sensor_id · frame  canonical semantics (§13.8)
             calibration sources                   e.g. /tf_static, CameraInfo topics
             pose sources                          e.g. /tf (map → base_link) or odometry
             canonical time per channel            which preserved timestamp
                                                   (e.g. Header.stamp, publish_time,
                                                   log_time) + its clock identifier
             segmentation policy                   whole recording | fixed windows | …
                                                   (window clock: Q4)
             payload extraction per schema         → declared media_type
steps      read messages with their preserved timing and ordering facts
           → take each observation's canonical timestamp per build_config; order per I-34
           → segment into half-open windows
           → interpret calibration and frame semantics (state "in effect" for a window
             may come from a message before it, e.g. latched /tf_static)
           → extract each in-boundary observation payload into a write-once
             OBSERVATION_PAYLOAD artifact
           → SceneManifest per window (RecordingSegmentSource + ProducerInfo)
outputs    complete SceneManifest set for (DV, scene, robot_run_id) + fingerprint
           + payload/manifest ArtifactRecords → REGISTER_SCENES
must not   branch on origin metadata, capture.source.kind or robot_platform; rename topics;
           sample, associate, interpolate poses or synchronize; set ego_pose_id unless the
           source itself associates a pose; skip a selected channel it cannot decode
```

**`RecordingEpisodeBuilder`** (`BUILD_RECORDING_EPISODES`)

```text
inputs     robot_run_id → resolve_recording(); RobotRunRecord facts
           build_config (part of the fingerprint):
             observation channels                  topics + field selection
             action channels                       topics + field mapping (e.g. which
                                                   /vehicle/control fields are actions)
             state channels
             task / outcome sources                an event channel or explicit config
             canonical time per channel            as for Scenes
             segmentation policy                   whole recording | event / mission
                                                   boundaries | …
outputs    complete EpisodeManifest set (step-8 schema) for (DV, episode, robot_run_id)
           + fingerprint + payload/manifest ArtifactRecords → REGISTER_EPISODES
must not   resample to a control or training rate (ALIGN_EPISODE is derived); hard-code
           robot-specific fields; default the source clock
```

A named per-robot channel profile may later supply `build_config`. It is
configuration only, normalized into the fingerprint, and never a DB entity
or a source-format switch.

### 29.11 Acquisition vs canonicalization vs derived

```text
ACQUISITION (L0 → L1)         decides how to preserve and capture
  which messages are preserved (losslessly), with which bytes
  which timing facts are preserved: receive time → log_time, upstream publication
    time → publish_time, source timestamps unchanged in the payload; the recording clock
  acquisition order and source/transport sequence evidence
  how schemas and encodings are persisted
  transport duplicate and gap integrity; backpressure; finalization; publication
  never: domain units, channel semantics, canonical observation time, sampling,
         DatasetVersion

CANONICALIZATION (L1 → L2)    decides what the recording means as domain data
  which recording channels become which domain channels (modality, sensor, frame role)
  which preserved timestamp is each channel's canonical observation time, in which clock
  canonical temporal order (I-34)
  Scene / Episode boundaries and unit keys
  calibration and coordinate-frame interpretation
  payload extraction into SceneOps-owned artifacts
  canonical identity and provenance (RecordingSegmentSource, ProducerInfo)
  never: lossy sampling, alignment, association, fusion, workflow preprocessing

DERIVED (L2 → L3)             decides what a workflow needs
  temporal alignment, resampling, nearest-frame association, interpolation
  synchronized sample views, sensor fusion, model-specific preprocessing
  label-set mapping, exports, indexes
  never: canonical membership, identity, or writing back into L1/L2
```

### 29.12 Batch / streaming equivalence

One logical source can enter in two ways:

```text
external dataset ─┬─ batch ─────────────────────────────────▶ recording A
                  └─ replay → ROS2 → Kafka → capture ───────▶ recording B
recording A / B → publish → REGISTER_ROBOT_RUN → same RecordingSceneBuilder, same build_config
```

The raw bytes of A and B may differ, and so may their recorder receive
times: replay pacing and transport latency change `log_time` legitimately.
**Semantic acquisition equivalence** therefore compares source-semantic
content, not acquisition timing. It is defined over the channels both
recordings include:

```text
equal       channel set, and per channel (topic, schema_name, schema_encoding,
            message_encoding)
            robot_id
            per channel, the source-semantic messages, compared as a multiset with
            occurrence identity (duplicates are counted, never collapsed). Each message
            is identified by:
              its source-semantic timestamp, where available (a timestamp the source
                message carries, e.g. Header.stamp, or an upstream publication time the
                source itself defines; when it lies inside the payload, the payload
                checksum already covers it)
              sha256(payload)
              its source/transport sequence position, where both recordings carry
                sequence information for the channel; the per-channel sequence order
                must then match as well

may differ  run_id; file bytes, chunking, compression, indexes; recorder log_time and
            the started_at / ended_at derived from it; publish_time where it is a
            transport or replay time; cross-channel write order; MCAP sequence fields
            that only one recording carries; schema definition text that differs only
            in formatting; metadata records and attachments (acquisition origin);
            capture.source.kind; recording URI, checksum and size; RobotRunManifest bytes
```

**Invariant (I-35).** Take two semantically equivalent recordings and build
them with the same producer and a `build_config` that takes canonical
observation times and unit boundaries from source-semantic timestamps. The
resulting canonical manifests are identical *except* for fields that depend
on the source revision: `RecordingSegmentSource` (`robot_run_id`,
`recording_artifact_id`, `recording_checksum`), the producer fingerprint,
and payload artifact ids if they derive from the run. Observations, their
canonical timestamps, channels, payload checksums, sizes and media types,
calibrations, poses and groups are equal. Anything a producer derives from
recorder receive time (`log_time`) depends on the acquisition and is outside
I-35. That includes RobotRun-clock segment windows under A2 (Q4).
Prerequisites:

- Canonical temporal order never depends on physical file layout (I-34).
- No writer synthesizes a source-semantic timestamp from wall-clock or
  replay-pacing time (R11).

The equivalence test is implemented in step 9 (§29.19).

### 29.13 External dataset acquisition tool

**Responsibility.** The tool converts an external dataset into *acquisition
input*. It does not produce canonical data. It knows nothing about
SceneManifest, SceneRecord, EpisodeManifest, DatasetVersion,
REGISTER_SCENES, validation, profiling, curation or inference.

```text
dataset adapter (nuScenes, LeRobot, …)
      ↓  acquisition events: topic · schema (name, encoding, definition)
      ↓  · message encoding · payload bytes (serialized once; source timestamps inside)
      ↓  · source time for pacing / simulated receive time · optional origin facts
      ├── MCAP sink         → local L1-conformant MCAP          (batch mode)
      └── ROS2 replay sink  → timed publication of the same bytes (streaming mode)
```

An acquisition event is a tool-internal type, not a SceneOps domain
abstraction. The adapter serializes each message once, as standard ROS 2
messages (camera: `sensor_msgs/msg/CompressedImage` with the source JPEG
bytes passed through; lidar: `sensor_msgs/msg/PointCloud2`; `/tf_static`,
`/tf`, `CameraInfo`; CAN telemetry on today's `/vehicle/*` topics). Both
sinks emit identical payload bytes, which makes §29.12 checkable. The MCAP
sink writes a deterministic simulated receive time into `log_time` (§29.5).
In replay mode, capture assigns the real receive time. A source
with several units maps one unit to one recording (for example one nuScenes
scene, or one LeRobot episode, per RobotRun). Every sweep or frame the
adapter converts is recorded. The adapter does not keep keyframes only.

**Boundary into SceneOps.** The tool never publishes, registers or calls
SceneOps APIs:

```text
batch      tool → local MCAP → python -m sceneops_integrations.recording publish
           → REGISTER_ROBOT_RUN
streaming  tool replay → ROS2 → streaming_bridge_node → Kafka → capture → publisher
           → REGISTER_ROBOT_RUN
```

**Placement: in this monorepo, dependency-isolated.** The tool lives at
`tools/dataset-acquisition/`: its own uv project and lockfile, not a
workspace member, with its own image (the existing precedent is
`tools/nuscenes-integration`, `tools/lerobot-integration`).

- E2E and clean-room runs need it pinned with the platform revision they
  validate.
- The L1 conformance suite (§29.5) must run against tool output and,
  once capture is corrected (step 9), capture output, in one CI.
- A separate repository would add version coordination with no consumer
  outside SceneOps.

Its dependency closure is the dataset SDKs plus MCAP and ROS 2 message
serialization. It has **no** dependency on `sceneops-core`, `-db`,
`-storage`, `-streaming` or `-integrations`, and an import-boundary test
enforces that (I-36). The replay sink needs a ROS 2 runtime and runs in the
ROS 2 image. Kafka-direct replay that bypasses the bridge is DEFERRED,
because the bridge is part of the acquisition system under test. The tool
replaces `tools/nuscenes-integration`, and its replay sink replaces
`ros2/nodes/can_replay_node.py` (§29.20).

### 29.14 Streaming responsibilities

SceneOps streaming (ADR-005 bridge, Kafka, capture, publisher hand-off)
owns:

```text
live intake            ROS2 subscription per a static channel registry; envelope build
timestamp preservation capture receive time → log_time; upstream publication (bridge
                       ingest) time → publish_time; source timestamps unchanged in the
                       payload; transport-only timing facts preserved in the recording
ordering               Kafka key = robot_run_id → one partition per run; per-run sequence
                       completeness (exact redelivery dropped, conflict or gap fails);
                       consumed order and transport sequence preserved as evidence
backpressure           bounded bridge and producer queues; fail loudly, never drop silently
                       (streaming-transport §14)
capture lifecycle      RUN_START / RUN_END control envelopes; in-memory CaptureSession (§11)
failure / restart      offsets committed only after durable finalization; in-flight sessions
                       lost on restart (known limitation; durable recovery DEFERRED, §26)
finalization           .partial → fsync → atomic rename
publication            finalized MCAP → Recording Publisher (manifest last) → optional
                       REGISTER_ROBOT_RUN submission
```

Streaming does **not** own segmentation, domain units, alignment, canonical
identity, or any DB write except through `REGISTER_ROBOT_RUN`.

Gaps for future work (step 9):

- Sensor, tf and CameraInfo channels are absent from the static bridge and
  capture registries.
- `/tf_static` needs latched (transient-local) QoS so static data is
  captured.
- Large payloads (camera, lidar) against Kafka message-size and throughput
  limits must be measured before any broker change (architecture rule 9).
- Capture → publish hand-off is manual (CLI). Automating it must keep the
  manifest-last protocol.
- Capture's `log_time` mapping must change to receive time, and the
  envelope `sequence_number` must be preserved (R4, R6, §29.20).

### 29.15 Annotations and source-defined groupings (Q1: decided by A8, §33.2)

Ground-truth annotations and the nuScenes keyframe `sample` grouping are
products of *labeling*, not acquisition facts: real robots never record
ground truth. At HEAD they reach detection and scenario workflows only
through the legacy nuScenes Scene integration, which A4 removes. Until Q1 is
decided:

- L1 recordings do not carry post-acquisition labels as sensor channels.
  Events recorded at acquisition time (R11) are different and remain L1
  data.
- `RecordingSceneBuilder` v1 emits no annotations and no keyframe groups
  unless the recording itself defines them (e.g. hardware sync groups).
- Detection over recording-derived Scenes needs a *derived* synchronized
  sample view. The keyframe-only view returns nothing for them.

Recommended direction, to be decided before step 10: **label import is a
separate ingress, anchored to L1 message identity**. A label set references
`(robot_run_id, topic, message occurrence identity, §29.12)`, survives re-canonicalization and
DatasetVersion changes, and serves human, automatic and external labels
alike. The external tool would then emit the dataset's labels as a
label file next to the recording, not inside it.

### 29.16 DatasetVersion implications

DatasetVersion is scope and membership over canonical Scenes and Episodes,
plus recomputed summaries (§16, unchanged). After A4 it never relates to a
RobotRun or an external source directly. Those relations exist only through
unit provenance.

| Field | Current use | Classification | Removed in |
|---|---|---|---|
| `raw_source_root_uri` | legacy `BUILD_SCENES` raw-log; API create/patch | remove during the acquisition-first refactor | step 7 |
| `source_dataset_id`, `source_dataset_version` | API create; converters (the Episode builder copies DV ids into EpisodeLineage, a separate field) | remove during the refactor | step 7 |
| `Dataset.type` / `DatasetType` | API; `INGEST_SCENES` dispatch | remove during the refactor | step 7 |
| `required_channels` | validation default; `DatasetInputRef` | later legacy debt (workflow configuration) | step 10 |
| `manifest_uri` | `DatasetInputRef`, quality API; points to the derived index | later legacy debt | step 10 (index-job merge) |
| `DatasetVersionRecord.metadata` / `Dataset.metadata` | free-form | later legacy debt (not in the §16 target) | step 10 |
| `keyframe_count` | registrar summary | canonical summary; revisit with Q1 | — |
| identity, `status`, `scene_count`, `observation_count`, `episode_count`, `observed_channels`, timestamps | — | genuinely canonical DatasetVersion state | — |

### 29.17 Superseded and amended sections

| Section | Effect of A4 |
|---|---|
| §2 | `external` source kind, `EXTERNAL_*` pipelines and decision 14 superseded; decisions 1–13 stand |
| §3.2 | `ExternalUnitSource`, Integration (ingest) rows superseded; `ExternalDatasetRef` is an integration-runtime locator (§29.9) |
| §4 | layer A no longer contains an ingest integration; canonicalization stages start at the RobotRun |
| §5 | external rows superseded |
| §6.1, §6.6, §6.7 | superseded (§29.2, §29.4) |
| §13.9 | external half superseded; recording half and §27.5 stand |
| §13.12 | debt items are resolved by *removing* the legacy producers (step 7), not by normalizing them |
| §14.1, §14.2, §15.2 | external source block and external source revision removed |
| §16 | "source format → ExternalUnitSource" row superseded; no source format exists on DatasetVersion or units |
| §17.1, §17.2 | superseded; §17.6 reduced to the recording pipelines and `REGISTER_ROBOT_RUN` |
| §18.1 (external line), §18.2 | superseded |
| §20.2 | "integration imports payloads" target homes superseded; categories still apply to recording builders and derived workflows |
| §20.3–§20.7 | superseded for ingestion; open identifiers and the integration boundary remain for EXPORT only |
| §21 | I-25 superseded; I-28, I-30 narrowed; I-31–I-37 added (§29.18) |
| §22.4–§22.6 | `INGEST_SCENES` → REMOVE (not RENAME); `EXTERNAL_*` NEW rows dropped; external-episode E2E dropped; `e2e_scene.sh` replaced by recording-scene E2E |
| §24 | steps 6–11 replaced by §29.19 |
| §25 | consequences extended by §29.22 |
| §26 | additions in §29.21 |
| §27.3 | `mcap_log_time` is the recording (receive-time) clock, not a source observation clock (§29.5 R4–R5). Segment windows on that clock are open question Q4 |
| §27.4 | nuScenes boundary paragraph superseded |
| §27.6 | external format identifiers leave canonical structures; INGEST resolution superseded |
| §28 | moot; its rule now holds structurally |

### 29.18 Invariants

Superseded and narrowed:

```text
I-25  SUPERSEDED. A new external dataset format adds an adapter to the external acquisition
      tool only; it adds nothing to SceneOps core, schemas, pipelines or workflows.
I-28  NARROWED. Source clock and producer identifiers remain canonical open identifiers.
      External format identifiers are integration-runtime identifiers only.
I-30  NARROWED. Holds structurally: canonical provenance has no external block, and
      RecordingSegmentSource carries no location or display name.
```

Added:

```text
I-31  The only ingress for acquired sensor and robot-state data is a registered RobotRun.
      Every canonical Scene and Episode has a RecordingSegmentSource. This does not exclude
      lineage roots that are not acquisition, such as labels (Q1).
I-32  Canonicalization never branches on acquisition origin, acquisition mode
      (capture.source.kind), robot_platform or recording metadata. Its output is a function
      of recording content (topics, schemas, payload bytes, preserved timing and ordering
      facts), producer semantics and normalized build_config.
I-33  An L1 recording preserves every available timing fact, each with its own meaning:
      MCAP log_time = recorder / capture receive time (the recording clock,
      "mcap_log_time"); publish_time = upstream publication time where the transport
      provides one, otherwise log_time; source observation timestamps stay in the source
      message, unrewritten, in their own clock (no epoch requirement). No writer
      synthesizes a source-semantic timestamp from wall-clock or replay-pacing time.
      Choosing a channel's canonical observation time is a canonicalization decision,
      declared in build_config with its clock identifier.
I-34  Capture / file order is acquisition evidence and is preserved where the format
      provides it, together with any source or transport sequence information. Canonical
      temporal order is defined by each observation's canonical timestamp, with ties broken
      by preserved sequence information and otherwise by per-channel acquisition order. It
      is never inferred solely from physical file order and never depends on chunking,
      compression or indexes.
I-35  Two semantically equivalent recordings (§29.12) built with the same producer and
      build_config yield canonical manifests that differ only in source-revision-dependent
      fields. (A5, §30.9: the comparison is of canonical semantic content; provenance and
      provenance-owned artifact ids, including payload artifact ids, may differ.)
I-36  External dataset tooling depends on no SceneOps package and produces only L0 input
      (ROS2 messages) or L1 input (a local MCAP). It never writes canonical manifests,
      records or ArtifactRecords.
I-37  An L1 recording encodes no canonicalization decision: no unit boundary, unit key,
      channel → semantics mapping, sampling decision or DatasetVersion reference.
```

Backing tests: I-31 and I-32 by registrar and builder tests plus import and
grep guards. I-33 by the L1 conformance suite applied to tool output (step 6)
and capture output (step 9). I-34 by builder tests that vary cross-channel
write order and chunking, and that cover equal-timestamp ties with and
without sequence information. I-35 by
the step-9 batch/streaming equivalence test. I-36 by an import-boundary test
on the tool project. I-37 by `RobotRunManifest` strictness (§8.2) and tool
golden tests.

### 29.19 Revised implementation sequence (replaces §24 steps 6–11)

Steps 0–5 are complete. Each step keeps §24's validation rule: focused unit
tests, real infrastructure where touched, and replacement of every E2E it
breaks (§23, rule 4).

```text
6. L1 contract + batch acquisition tool
   core      L1 conformance suite (§29.5), applied to tool output first and to capture
   (small)   output once capture is corrected (step 9); MCAP_LOG_TIME_CLOCK moved to a
             shared recording/clock module; publisher and RobotRunManifest unchanged
   tooling   tools/dataset-acquisition (isolated): nuScenes adapter → acquisition events
             → MCAP sink: cameras, lidar, /tf_static, CameraInfo, ego pose, CAN telemetry
             (today's /vehicle/* topics), acquisition-origin metadata; mission/task events
             with source-timeline timestamps (R11); import-boundary test (I-36)
   E2E       tool → local MCAP → publisher → REGISTER_ROBOT_RUN → resolve_recording on
             real MinIO/PostgreSQL (the first sensor-bearing RobotRun)

7. Recording → canonical Scene, and removal of external/legacy Scene ingestion
   core      BUILD_RECORDING_SCENES + RECORDING_SCENE_BUILDING (§29.10); OBSERVATION_PAYLOAD
             extraction; lidar payload representation decided (Q2); I-34 ordering
   removal   INGEST_SCENES, BUILD_SCENES, raw-log mode, RawLog*, RawLogAdapter, legacy
             Scene manifests, DATASET_SCENE_INGESTION, RAW_LOG_SCENE_BUILDING, nuScenes INGEST
             integration + tools/nuscenes-integration, DatasetType dispatch;
             ExternalUnitSource and every external branch (§29.9); DV raw_source_root_uri,
             source_dataset_*, Dataset.type
   E2E       recording-scene E2E (tool batch → RobotRun → Scenes → validate/profile)
             replaces e2e_scene.sh and e2e_scene_rawlog.sh

8. Canonical Episode + recording → Episode
   core      source-faithful EpisodeManifest (§13.10) on step-4 primitives;
             RecordingEpisodeBuilder (§29.10); REGISTER_EPISODES registrar ownership
             + §18.3 + fail-loud; drop EpisodeRecord.status / EpisodeStatus; action/observation
             mapping and canonical time source per channel as build_config (the
             builder no longer treats log_time as source time); RosbagAdapter topic
             defaults removed
   E2E       recording-episode E2E replaces the episode-building / robot-learning E2Es

9. Streaming acquisition for sensor-bearing recordings + replay + equivalence
   tooling   ROS2 replay sink (replaces can_replay_node.py)
   core      capture log_time → receive time and envelope sequence_number preserved
             (R4, R6); bridge + capture registries extended (sensor, /tf_static latched,
             CameraInfo);
             Kafka large-message behavior measured before any configuration change;
             optional automatic capture → publish hand-off (manifest-last preserved)
   E2E       batch vs streaming equivalence (I-35) through the step-7 builder

10. Derived workflow cleanup
    detection on recording Scenes via a derived synchronized-sample view; media_type dispatch
    for lidar; label ingress (after the Q1 decision); scenario / readiness on recording Scenes;
    DV required_channels, manifest_uri, metadata; dataset-index job merge; reserved JobTypes
    and dead Protocols

11. E2E / clean-room consolidation + canonical baseline
    raw nuScenes mini → tool (batch and replay) → RobotRuns → Scenes / Episodes → quality →
    curation / detection / evaluation → LeRobot export; regenerated canonical baseline;
    docs/architecture/* confirmed current
```

Dependencies:

| Dependency | Consequence |
|---|---|
| Step 7 needs a sensor-bearing recording, which only step 6 produces | 6 → 7 |
| Removing legacy Scene producers breaks e2e_scene*, and only the recording-scene E2E replaces them | removal is bundled into 7, not done earlier |
| Step 8 needs step-4 primitives (done). Mission segmentation needs source-timeline events (step 6). CAN-only recordings already suffice | 6 → 8; 7 and 8 are independent and may run in either order |
| The equivalence test needs both acquisition modes and a canonical builder | 6, 7 → 9 |
| Today's Episode building reads `log_time` as source time. Correcting capture's `log_time` first would shift Episode timestamps | the capture correction (9) lands after the Episode builder reads source-semantic times (8): 8 → 9 |
| Detection restoration needs recording Scenes (7), a derived sample view, and ground truth (Q1) | 7 + Q1 → 10 |
| Reserved-JobType / Protocol removal has no dependency | any time, as an independent change |
| The canonical baseline is invalidated by 7 and 8 | regenerated in 11; intermediate steps run on reset dev state |

Work tracks:

```text
core platform        6 (conformance, clock module) · 7 · 8 · 9 (bridge, capture, hand-off) · 10
external tooling     6 (batch adapter + MCAP sink) · 9 (replay sink)
interop / export     LeRobot EXPORT unchanged (re-pointed to the step-8 EpisodeManifest when
                     its input changes); external-ingest interop DEFERRED
```

### 29.20 Code migrations implied by A4 (not implemented by this amendment)

```text
provenance     delete ExternalUnitSource, ExternalSourceRevision and the unions; identity
               recording-only; SceneLineage.source = RecordingSegmentSource          (step 7)
scenes         SceneRecord: drop source_kind, external_format, ck_scenes_source_projection;
               robot_run_id and window columns NOT NULL (destructive migration; no registered rows exist);
               registrar external branch; API external_format filter; analytics column;
               keyframes.annotation_source; testing fixtures                           (step 7)
integration    ExternalDatasetRef → sceneops_core.integration_runtime; remove
               IntegrationOperation.INGEST, the nuScenes INGEST runtime (raw_log.py,
               scene_ingest.py, service modes), tools/nuscenes-integration, worker
               nuscenes_ingestion, scripts/e2e/*nuscenes_container*                    (step 7)
legacy scene   INGEST_SCENES, BUILD_SCENES, RawLog*, RawLogAdapter(+Factory),
               sceneops_core.scenes.legacy, LEGACY_SCENE_MANIFEST, DATASET_SCENE_INGESTION,
               RAW_LOG_SCENE_BUILDING, DatasetType, RosbagAdapter Scene topic map       (step 7)
datasets       DV raw_source_root_uri, source_dataset_*, Dataset.type                  (step 7);
               required_channels, manifest_uri, metadata                               (step 10)
episodes       RosbagAdapter defaults and steering/throttle/brake → build_config;
               EpisodeLineage → RecordingSegmentSource + ProducerInfo                  (step 8)
clock          MCAP_LOG_TIME_CLOCK out of episodes.alignment.config                    (step 6)
acquisition    can_replay_node.py → tool replay sink; /mission/status wall-clock time →
               source-timeline events (R11); bridge/capture registries for sensor channels
                                                                                   (steps 6, 9)
capture        log_time = capture receive time (today: envelope source_timestamp_ns);
               publish_time = bridge ingest time (unchanged); envelope sequence_number
               preserved in the recording; streaming-transport §20 updated            (step 9)
docs           scene-domain, data-model, external-integration-runtime,
               dataset-interoperability, streaming-transport, storage-layout,
               jobs-and-pipelines updated in the step that changes each contract
```

### 29.21 Open questions and deferred work

Open questions that block a later step:

```text
Q1  DECIDED by A8 (§33.2).
    Ground truth and keyframe groupings after the nuScenes Scene integration is removed
    (§29.15). Labels are separate lineage-bearing label sets anchored on canonical
    observations; sample selection and synchronization are a derived SceneSampleView.
Q2  DECIDED by A5 (§30.4).
    Canonical lidar payload representation (PointCloud2 bytes vs a declared SceneOps
    point layout, and its media_type). Blocks step 7's payload extraction; decided in step 7.
    The step-6 tool records standard PointCloud2, which keeps both options open.
Q3  Kafka transport of large sensor messages (size limits, throughput). Blocks step 9 only;
    measure first.
Q4  DECIDED by A5 (§30.2).
    Segment-window clock. A2 (§27.2) copies RecordingSegmentSource.source_clock from the
    RobotRun, which under §29.5 R5 is the recording (receive-time) clock. Windows
    on that clock depend on acquisition timing (outside I-35). Source-semantic
    segmentation would require the window clock to be a canonical channel clock.
    Blocks step 7's segmentation; decided in step 7, as an A2 amendment if changed.
```

Not blocking (defaults chosen): `build_config` comes from explicit pipeline
parameters, and named robot profiles come later. Payload artifact-id
derivation is decided in step 7, and either run-scoped or content-derived
ids satisfy I-35.

Added to §26:

```text
external canonical ingest (readmission requires a new amendment, §29.9)
Kafka-direct dataset replay bypassing the ROS2 bridge
RobotRunManifest v2 display-only acquisition-origin descriptor
non-MCAP L1 recording formats
calibration that changes within one Scene window (v1: constant per Scene)
```

### 29.22 Consequences

Positive:

- One ingress for acquired data, one builder per domain, one canonical path
  from RobotRun down. External data exercises the production path instead of a parallel
  one.
- Format knowledge is confined to a tool outside the platform. Core
  identity, records and fingerprints hold no external format.
- Streaming and batch acquisition share every guarantee after publication.
  Equivalence is a testable invariant.
- The L1 contract is explicit. The code meets most of it; capture's
  `log_time` mapping is the main deviation (§29.20). No `RobotRunManifest`
  change is needed.
- The external canonical surface removed in step 7 never had registered
  data, so dropping it migrates nothing.

Negative / costs:

- External datasets lose structure that timed messages cannot represent,
  unless it is recorded explicitly. Labels and keyframe groupings need their
  own path (Q1). Detection evaluation stays unavailable until step 10.
- Test-data ingest costs more: convert to a recording, publish, then
  canonicalize. Payload bytes exist twice, in the recording and in the
  extracted canonical payloads (§27.5, unchanged).
- Detection needs a derived synchronized-sample view for recording Scenes.
  It can no longer lean on source keyframes.
- Writer obligations (R4, R6, R9, R11) are verified by tests, not by
  publication checks.
- The tool is a new, separately locked project with ROS 2 serialization
  dependencies, and its replay sink needs the ROS 2 image.
- Streaming sensor-bearing data puts new load on Kafka, which is unmeasured
  (Q3).

---

## 30. Amendment A5: recording Scene canonicalization (step 7)

### 30.1 Builder boundary

```text
BUILD_RECORDING_SCENES(dataset_id, dataset_version, robot_run_id, build_config)
  resolve_recording(robot_run_id)          the only way the builder gets bytes (§12.4)
  check_l1_recording(local copy)           the step-6 conformance suite; violations fail
  plan_recording_scenes(copy, revision, build_config)
                                           pure, DB-free: every SceneManifest of the
                                           recording scope + the payload plan
  payload bytes -> OBSERVATION_PAYLOAD ArtifactRecords -> manifests -> SCENE_MANIFEST
                                           ArtifactRecords                    (producer-owned)
  result.manifest_artifact_ids             the complete scope -> REGISTER_SCENES
```

The job accepts no recording URI or local path (`RecordingConsumerJobParams`
rejects them). The DatasetVersion only scopes where manifests are
published; it is not a build input and never enters manifest bytes. One
pipeline run builds one RobotRun (§17.5 rule 1):

```text
RECORDING_SCENE_BUILDING
  build_recording_scenes -> register_scenes -> validate_scene, profile_scene (optional)
```

Messages are read through one reader, `sceneops_integrations.recording.reader`,
which the conformance suite shares: topic, schema, encodings, payload,
`log_time`, `publish_time`, MCAP `sequence`, file position overall and per
channel. Decoding uses the schema embedded in the recording. Only ROS 2
(`cdr` / `ros2msg`) is decoded; a selected channel in any other encoding
fails the build.

Producer: `sceneops.recording_scene_builder`, `semantics_version = 1`.

### 30.2 Q4 decision: the segment window clock

**Decision.** A Scene window is a half-open interval in exactly one clock,
the **segmentation clock**, declared by the segmentation policy in
`build_config`. `RecordingSegmentSource.source_clock` is that clock. It may
be:

```text
mcap_log_time       recorder receive time (the recording clock)
mcap_publish_time   MCAP publish_time
a source clock      the clock the build configuration declares for a header-stamp
                    time policy (e.g. "sensor.header_stamp")
```

Placement rule. Every included channel and pose source must have a
timestamp on the segmentation clock. Either its canonical observation time
is in that clock, or the segmentation clock is a recording clock that every
message carries (`mcap_log_time`, `mcap_publish_time`). Any other
combination is a configuration error. An observation is assigned to a window
by its segmentation-clock timestamp. It keeps its own canonical timestamp
and channel clock, and timestamps in other clocks are never compared with
the window (the manifest validator checks only timestamps in the window
clock).

Consequences:

- §27.2's "copied from the RobotRun" is superseded. The registrar no longer
  compares a manifest's window clock with `RobotRunRecord.source_clock`. It
  still pins the recording bytes (`recording_checksum`).
- Windows are derived from message timestamps, never from
  `RobotRun.started_at` / `ended_at` (§27.3 unchanged).
- A build that uses `log_time` (as segmentation clock or as a channel time)
  requires the RobotRun's recording clock to be `mcap_log_time`.
- With a source-semantic segmentation clock, unit boundaries and keys come
  from source timestamps, so I-35 covers them. A `log_time` segmentation
  stays outside I-35, as §29.12 states.

### 30.3 Segmentation policy v1

```text
fixed_duration { clock, duration_ns > 0 }
  origin     the earliest segmentation-clock timestamp of any included observation
  window k   [origin + k·duration_ns, origin + (k+1)·duration_ns)
  Scenes     one per window that holds at least one observation; empty windows
             are not Scenes
  unit_key   segment-<k, 6 digits>
  poses      assigned to windows by their segmentation-clock timestamp; a pose in no
             Scene window belongs to no Scene
```

The policy depends only on recording content and `build_config`, never on
the DatasetVersion or execution state. A recording that yields no Scene
fails (§18.3).

### 30.4 Q2 decision: canonical payloads

**Lidar (and any other channel built with `ros2_message`).** The canonical
payload is the recorded message bytes exactly as serialized: CDR with its
encapsulation header, as MCAP `Message.data` holds them. Its media type
names the ROS 2 message type:

```text
application/x.ros2-cdr.<package>.msg.<type, lowercased>
e.g. application/x.ros2-cdr.sensor_msgs.msg.pointcloud2
```

Rationale: it is lossless and source-faithful. It keeps the
`PointCloud2` field layout (`fields`, `point_step`, endianness, `is_dense`)
self-describing for any layout, so no SceneOps point layout is designed
before a second consumer needs one (architecture rule 5). Canonicalization
does no conversion, and the payload checksum equals the sha256 of the L1
message bytes, which is the occurrence identity of §29.12. Cost: a downstream
reader needs a ROS 2 CDR decoder and the standard message definition, which
the media type names. A normalized point layout, if one becomes necessary,
is a derived representation (step 10 media-type dispatch).

**Camera (`compressed_image`).** The canonical payload is the `data` bytes of
a `sensor_msgs/msg/CompressedImage`, unchanged. Nothing is decoded or
re-encoded. The media type comes from `format`: `image/jpeg` for `jpeg` / `jpg`
or a `... jpeg compressed ...` format, and `image/png` for `png`. The payload
must start with that format's signature, and any other format fails.
`sensor_msgs/msg/Image` (raw) has no v1 extraction.

### 30.5 Payload identity and publication

```text
artifact_id   payload-<sha256(canonical_json{payload_id_schema
                 "sceneops.observation_payload_id/v1", robot_run_id, topic,
                 channel_index, extraction})[:32]>
channel_index the message's occurrence index among the messages of its own topic, in
              that topic's acquisition order (I-34 evidence of this recording)
key           {artifact_root}/observation_payloads/{robot_run_id}/{artifact_id}
record        OBSERVATION_PAYLOAD, owner robot_run/<robot_run_id>, sha256, size, media type
```

The id is a property of one recording message and one extraction. It
depends on no build configuration, so a rebuild with another window length
or another time policy reuses every payload, and no id can name different
bytes under two configurations. `channel_index` counts only the topic's own
messages, so how topics are interleaved in the file (cross-channel write
order) never affects it. It is deliberately not a rank in canonical time
order: that order depends on the configured time policy. The id is scoped to
the RobotRun: an equivalent recording of another acquisition owns other
payload artifacts (§30.9). Publication is write-once. The same id with the
same bytes is reused, and the same id with different bytes, or an existing
ArtifactRecord whose location, checksum, size or media type differs, fails
the job. Manifest ArtifactRecords are deterministic too:
`scene-manifest-<sha256(scene_id, manifest checksum)[:32]>`. A retry after
a crash between writing bytes and committing records therefore converges.
No content-addressed deduplication across messages or runs exists (§27.5).

### 30.6 Interpretation rules (v1)

```text
channels       build_config.channels: topic (verbatim), modality, optional sensor_id,
               time policy, extraction, camera_info_topic (cameras)
frames         the channel frame is the message header frame_id, constant per channel;
               build_config names the ego frame (required) and world frame (optional);
               channel frames take the sensor role
calibration    static transforms on build_config.calibration.static_transform_topics
               (default /tf_static); constant for the whole recording (§29.21 deferred
               item); the extrinsic of a camera / lidar / radar frame is required and
               must be relative to the ego frame (chains are not interpreted in v1)
intrinsics     CameraInfo k -> camera_intrinsic, width / height -> image_size; constant;
               non-zero distortion, non-identity rectification or P != [K|0] fail
               (SceneManifest v1 cannot express them)
poses          every parent -> child transform of a configured TFMessage pose source is a
               ScenePose at its own stamp; no interpolation; observations get no
               ego_pose_id (the recording does not associate one)
observation id <topic slug>-<rank>, rank in the channel's canonical order: timestamp,
               then MCAP sequence when every message of the channel carries one, then
               per-channel file order (I-34)
groups / annotations   none (§29.15, Q1)
```

**Compatibility.** Removing the external source kind changes no serialized
byte of recording provenance. `RecordingSegmentSource` /
`RecordingSourceRevision` keep `source_kind = "recording"`, the unit-id
document keeps its `source_kind` member, and `fingerprint_schema`,
`unit_id_schema` and recording fingerprints and ids are unchanged. No
schema version is bumped.

### 30.7 Removal and schema changes (implements §29.9, §29.16, §29.20 step-7 rows)

```text
removed   ExternalUnitSource, ExternalSourceRevision, UnitSource / SourceRevision unions,
          UnitSourceKind, project_unit_source; INGEST_SCENES, BUILD_SCENES,
          DATASET_SCENE_INGESTION, RAW_LOG_SCENE_BUILDING; sceneops_core.scenes.legacy;
          RawLogManifest / RawLogFrameIndex / RawLog source enums; the worker raw-log
          Scene builder, observation adapters, ObservationArtifactStore; the nuScenes
          INGEST runtime, its HTTP service and tools/nuscenes-integration; the worker
          integration executors and their settings (no remaining caller);
          IntegrationOperation.INGEST; DatasetType; ArtifactKinds legacy_scene_manifest,
          raw_log_manifest, raw_log_frame_index, raw_sensor_frame, scene_sample_manifest,
          scene_segment_index; RosbagAdapter.build_raw_log and its topic renaming
moved     ExternalDatasetRef -> sceneops_core.integration_runtime
scenes    source_kind, external_format, ck_scenes_source_projection and
          ck_scenes_declared_window dropped; source_unit_key -> unit_key; robot_run_id
          and window columns NOT NULL; ck_scenes_segment_window
datasets  dataset_versions.raw_source_root_uri, source_dataset_id, source_dataset_version
          and datasets.type dropped
migration c3f1a7d5e902 refuses to run while an external Scene or an artifact of a
          removed kind exists (reset development state instead); converts recording
          rows in place
```

### 30.8 Invariants

```text
I-38  A recording-derived unit's window is a half-open interval in the one segmentation
      clock its producer's build configuration declares. Every timestamp the unit holds in
      that clock lies inside it, and no timestamp in another clock is compared with it,
      converted to it, or used to derive it. Windows are never derived from RobotRun
      started_at / ended_at.
I-39  A rebuild of the same RobotRun (same recording revision) with the same producer and
      build_config yields byte-identical manifests and the same Scene ids, unit keys and
      payload artifact ids; with another build_config it reuses every payload artifact id.
      No canonical identity depends on cross-channel write order, chunking or indexes.
```

Backing tests: `apps/worker/tests/scenes/test_recording_scene_identity.py`
(I-35 / I-39), core manifest window tests, builder segmentation tests
(half-open boundaries, multi-clock Scenes, `log_time` segmentation keeping
source stamps), and the recording-scene E2E.

### 30.9 Semantic equivalence versus provenance identity

I-35 compares *canonical semantic content*, not manifest bytes or artifact
ids. For two semantically equivalent recordings (§29.12) of different
acquisitions (for example batch acquisition and stream replay → capture),
built by the same producer with the same `build_config`:

```text
equal (when canonical time and segmentation come from source-semantic timestamps)
  build_config · segment windows [start, end) and their clock · unit keys
  channels · coordinate frames · calibrations · poses · groups · annotations
  observations: observation ids, channels, canonical timestamps, calibration ids,
                image sizes, payload checksum / size / media type
  the logical payload bytes behind each observation

differ (provenance and provenance-owned identity)
  RecordingSegmentSource robot_run_id, recording_artifact_id, recording_checksum
  producer_fingerprint (it covers the source revision)
  payload artifact ids (§30.5: scoped to the RobotRun)
  manifest bytes and checksums, manifest ArtifactRecord ids
  Scene ids where both builds register into one DatasetVersion (robot_run_id is
  part of unit identity, §18.1)
```

So semantic equivalence is not identical manifest bytes, not identical
ArtifactRecord ids and not identical acquisition provenance. Each acquisition
owns its own physical artifacts. Anything derived from recorder receive time
(`mcap_log_time` segmentation or time policies) stays outside I-35 (§29.12).
`sceneops_core.scenes.testing.semantic_scene_content` is the executable
form of this projection, for the step-9 equivalence test and the builder
tests.

Same-acquisition determinism is stronger and unchanged (I-39). A rebuild of
the same RobotRun keeps byte-identical manifests and every id.

---

## 31. Amendment A6: recording Episode canonicalization (step 8)

### 31.1 Builder boundary

```text
BUILD_RECORDING_EPISODES(dataset_id, dataset_version, robot_run_id, build_config)
  resolve_recording(robot_run_id)          the only way the builder gets bytes (§12.4)
  check_l1_recording(local copy)           the step-6 conformance suite; violations fail
  plan_recording_episodes(copy, revision, build_config)
                                           pure, DB-free: every EpisodeManifest of the
                                           recording scope + the payload plan
  payload bytes -> OBSERVATION_PAYLOAD ArtifactRecords -> manifests -> EPISODE_MANIFEST
                                           ArtifactRecords                    (producer-owned)
  result.manifest_artifact_ids             the complete scope -> REGISTER_EPISODES

RECORDING_EPISODE_BUILDING
  build_recording_episodes -> register_episodes -> validate_episode, profile_episode (optional)
```

The job accepts no recording URI, path or robot id (`RecordingConsumerJobParams`
rejects them) and dispatches on nothing but `build_config`. One pipeline run
builds one RobotRun; several RobotRuns contribute to one DatasetVersion through
several runs (§17.5 rule 1). Messages are read through the shared reader
`sceneops_integrations.recording.reader`, the same one the Scene builder and the
conformance suite use. The builder never reads a Scene, and the Scene builder
never reads an Episode: the two are siblings over the same RobotRun (§13.1).

Producer: `sceneops.recording_episode_builder`, `semantics_version = 1`.

### 31.2 Canonical Episode

> **A canonical Episode is a source-faithful, task/behavior-oriented
> projection of one RobotRun window.** It preserves the recorded observation,
> state, action and task/event streams, each on its own declared clock.
> **Canonical Episode preserves asynchronous source streams. Temporal
> alignment is a derived L3 operation.**

`EpisodeManifest` (`sceneops.episode_manifest/v1`):

```text
lineage        EpisodeLineage { source: RecordingSegmentSource, producer: ProducerInfo }
streams[]      { topic (verbatim), role: observation | state | action | event,
                 schema_name (recorded), source_clock, fields[] {name, path},
                 has_payload }
observations[] states[] actions[] events[]
               EpisodeOccurrence { occurrence_id, topic, timestamp_ns,
                                   values {field name -> value}, payload? }
```

A value is kept as decoded from the recorded message: bool, integer, finite
float, string, or a list of numbers. A field that resolves to a nested message
or raw bytes is a configuration error; NaN / Infinity are not representable in
v1 and fail the build. The manifest holds no DatasetVersion, episode id,
status, task, outcome, control frequency, frame count or execution context.

### 31.3 Build configuration

```text
RecordingEpisodeBuildConfig
  streams[]   topic · role (observation | state | action) · time policy
              · decoding (ros2 | json_string) · fields[] {name, path}
              · payload (observation only: compressed_image | ros2_message)
  events[]    topic · time policy · decoding · fields[] (≥ 1)
  segmentation whole_recording {clock} | fixed_duration {clock, duration_ns}
              | event_markers {event_topic, key_field, state_field,
                               start_values[], end_values[]}

time policy   header_stamp | log_time | publish_time | payload_field {field}
              + the clock identifier it is in (log_time = mcap_log_time,
              publish_time = mcap_publish_time; a source clock otherwise)
```

`json_string` reads a JSON object from a `std_msgs/msg/String`, a recording
convention for messages without a standard ROS 2 type (the step-6
`/vehicle/control` and `/mission/status`). Steering/throttle/brake, odometry
fields or a mission topic are one configuration, never Episode-model fields.
`normalized()` (defaults explicit, streams/events by topic, fields by name,
marker values sorted) is `ProducerInfo.build_config`, so every output-affecting
choice is in the fingerprint; execution context is rejected (§27.7).

### 31.4 Segmentation and window clock

§30.2 applies unchanged: an Episode window is a half-open interval in exactly
one segmentation clock, `RecordingSegmentSource.source_clock`. Every stream
and event source must be placeable on it (its time is in that clock, or the
clock is `mcap_log_time` / `mcap_publish_time`). Windows are derived from
message timestamps, never from `RobotRun.started_at` / `ended_at`.

```text
whole_recording  one window [earliest, latest + 1) of every included message
                 unit_key  recording
fixed_duration   [origin + k·d, origin + (k+1)·d) from the earliest included
                 message; windows without a message are not Episodes
                 unit_key  segment-<k, 6 digits>
event_markers    one window per start / end marker pair of one task key, read
                 from a configured event source in canonical marker order:
                 [start marker, end marker + 1), so the end marker belongs to
                 its Episode; the clock is the event source's clock; an end
                 without a start, a second start, or a start never ended fails
                 unit_key  task-<key>-<occurrence, 3 digits>
```

A message belongs to a window by its segmentation-clock timestamp; windows of
different task keys may overlap. A build that yields no Episode fails (§18.3).

### 31.5 Streams

```text
observation  sensor or perception streams; with a payload extraction each
             occurrence references a SceneOps-owned OBSERVATION_PAYLOAD
state        recorded robot / environment state (odometry, joints, battery, ...)
action       recorded control / action events (commands, actuator feedback)
event        recorded task / event markers (mission running / completed, ...)
```

Every occurrence keeps its own canonical timestamp in its stream's clock.
Ordering and identity follow I-34: `occurrence_id = <topic slug>-<rank>`, the
rank in the stream's canonical order over the whole recording (timestamp, then
MCAP sequence when every message carries one, then per-topic file order).
Duplicate source events stay separate occurrences. No universal action vector,
no fixed rate and no `[action_dim]` shape is imposed. Only facts the recording
carries are kept: no success label, reward, outcome, subtask or language
instruction is manufactured; adding one is a separate, lineage-bearing import
(Q1, §29.15).

### 31.6 Payloads shared with Scenes

Episode observation payloads use the §30.4 extractions and the §30.5 identity
unchanged. The id depends only on `(robot_run_id, topic, channel_index,
extraction)` and the ArtifactRecord is owned by the RobotRun, so a Scene and an
Episode that extract the same recorded message the same way reference **one**
artifact; whichever build runs first creates it and the other reuses it
(write-once, verify-or-conflict). This is correct because ownership is the
RobotRun, not a domain unit. Payload extraction, identity and publication live
in `sceneops_worker.recordings`, shared by both builders.

### 31.7 Interpretation rules (v1)

- Every configured topic must be in the recording, and every selected field
  must resolve in every message; otherwise the build fails.
- `log_time` (as a time policy or segmentation clock) requires the recording
  clock `mcap_log_time`.
- A source-clock timestamp is never converted to another clock.
- Marker key and state values must be strings.

### 31.8 No alignment; Episode ↔ AlignedEpisode

Canonicalization never resamples, interpolates, forward-fills, pads,
associates nearest frames, stacks windows or puts streams on a common
timeline. `ALIGN_EPISODE` owns all of it:

```text
Episode (canonical revision, pinned by manifest_artifact_id + checksum)
  -> align_episode(manifest, TemporalAlignmentConfig, TemporalSourceContext, episode_id)
  -> AlignedEpisode (derived, L3)
```

`align_episode` (alignment semantics `v2`) aligns observation and state streams
as observation channels and action streams as action channels, named
`<topic>#<field>` (a payload stream as `<topic>`, a reference value carrying
the payload artifact id); boolean and string fields and event streams are not
aligned. It aligns on one clock (default: the Episode window clock) and rejects
any stream on another clock. Default association comes from the value kind and
role, never from a channel-name convention (action → previous, else nearest).
The timeline spans the Episode window when aligning on the window clock,
otherwise the aligned samples' extent. Unpinned `ALIGN_EPISODE` resolves the
revision the EpisodeRecord points to (§14.4), never "the latest artifact".
`task` / `outcome` on `AlignedEpisode` remain L3 fields; a canonical Episode
never fills them.

### 31.9 Provenance

`EpisodeLineage = { source: RecordingSegmentSource, producer: ProducerInfo }`,
the same composition as Scenes (§14.2). The fingerprint re-derives from the
manifest's own source revision. No rosbag path, raw-log id, dataset locator,
acquisition origin, robot id or mission id is canonical Episode provenance.

### 31.10 Identity

```text
episode_id                 canonical_unit_id(domain="episode", dataset_id,
                           dataset_version, robot_run_id, unit_key)        (§18.1)
EPISODE_MANIFEST artifact  episode-manifest-<sha256(episode_id, manifest checksum)[:32]>
manifest key               {dataset_root}/{dataset_id}/versions/{v}/episodes/{episode_id}/
                           manifest-<sha256>.json                          (write-once)
```

The same RobotRun revision and build configuration rebuild byte-identical
manifests, the same Episode ids and unit keys, and the same payload and
manifest artifact ids; another build configuration reuses every payload id.

### 31.11 Registration and record

`REGISTER_EPISODES` mirrors `REGISTER_SCENES` (§17.5, §18.3, §30): verify each
EPISODE_MANIFEST (pinned checksum and size, strict canonical parse, fingerprint,
every payload reference); one RobotRun and one fingerprint per input; the
manifests' recording checksum equals the RobotRun's; then, under the
DatasetVersion row lock, apply §18.3 to `(DatasetVersion, robot_run_id)` and
recompute `episode_count` from membership. The registrar takes the row lock
before inserting.

`EpisodeRecord` projects one revision: `episode_id`, `dataset_id`,
`dataset_version`, `robot_run_id`, `unit_key`, `producer_fingerprint`,
`manifest_artifact_id`, `manifest_checksum`, `window_clock`,
`window_start/end_timestamp_ns`, per-role observed topics and counts,
`registered_at` / `updated_at`. Removed: `status` / `EpisodeStatus` (§13.4),
`task`, `outcome`, `raw_log_id`, `robot_id`, `mission_id`,
`episode_manifest_uri`, channels, `control_frequency_hz`, `frame_count`,
`started_at` / `ended_at`, `metadata`. Episode run records pin
`manifest_artifact_id` + `manifest_checksum`; readiness counts only runs of the
current revision (§13.4).

Migration `b8e4d2a6c917` rebuilds `episodes` with foreign keys to
`dataset_versions`, `robot_runs` and `artifacts`, adds the run-record pin and
its check constraint, and refuses to run while any legacy Episode or Episode run
record exists (reset development state instead).

### 31.12 Removed

```text
BUILD_EPISODES, REGISTER_EPISODE, RAW_LOG_EPISODE_BUILDING  -> BUILD_RECORDING_EPISODES,
                                                               REGISTER_EPISODES,
                                                               RECORDING_EPISODE_BUILDING
EpisodeBuilder, EpisodeSegmenter, EpisodeSource, EpisodeSegmentationConfig,
EpisodeObservationFrame, EpisodeActionFrame, EpisodeStatus, legacy EpisodeLineage
RosbagAdapter.extract_episode_source and its sensor-topic defaults (RosbagAdapter
  remains only the derived robot-telemetry projection of INGEST_ROBOT_STATES)
sceneops_core.observations.schemas (RawSensorFrameManifest)
"latest EPISODE_MANIFEST by created_at" source resolution
```

`EpisodeOutcome` remains the vocabulary of derived learning workflows.

### 31.13 Invariants

```text
I-40  A canonical Episode keeps every recorded occurrence of its configured streams inside
      its window at the occurrence's own canonical timestamp, in the stream's declared clock.
      No canonical Episode is resampled, interpolated, forward-filled, padded, associated
      or synchronized; duplicates stay separate occurrences.
I-41  Episode semantics (stream roles, fields, time policies, segmentation) come only from
      build_config; no topic, field name or action schema is built into the Episode model.
I-42  Scene and Episode canonicalization of one RobotRun are independent: neither reads the
      other's manifests or records, and either may run first with identical results.
      Payloads they both extract are the same RobotRun-owned artifacts.
I-43  A canonical Episode carries only recorded facts: no label, outcome, reward or
      instruction is manufactured during canonicalization.
```

Backing tests: `apps/worker/tests/episodes/test_recording_episode_builder.py`
(I-34, I-39, I-40–I-43, §31.4 segmentation and clocks, failures),
`apps/worker/tests/episodes/test_recording_episode_vertical_integration.py`
(real PostgreSQL + MinIO: pipeline contract, retry, conflict / replacement,
partial-write retry, concurrent registration, Scene / Episode independence),
core manifest / config / alignment tests, and `make e2e-recording-episode`.


---

## 32. Amendment A7: streaming acquisition and batch/streaming equivalence (step 9)

### 32.1 Scope and result

A7 implements §29.14 and the step-9 row of §29.19. It does not change the
Scene or Episode builders or contracts. The same logical source, acquired in
batch and by stream replay, produces semantically equivalent recordings (§29.12)
and, under source-timestamp build configurations, equivalent canonical Scene and
Episode content (I-35, §30.9). Builds configured from `mcap_log_time` or `mcap_publish_time`, both
acquisition-dependent, are outside that guarantee. The measured result is in §32.9. A7 also fixes the
meaning of `publish_time` on the streaming path (§32.2) and makes a zero-stamped `/tf_static` legal
(§32.3); neither changes a Scene or Episode contract.

```text
batch      dataset → acquisition tool → MCAP ───────────────────────┐
streaming  dataset → acquisition tool replay → ROS 2 → bridge → Kafka│→ L1 conformance
             → capture → MCAP ───────────────────────────────────────┘   → publish → REGISTER_ROBOT_RUN
```

### 32.2 Timing and ordering rules (supersede the §29.5 R4 / R6 / R11 "HEAD" notes)

```text
log_time      capture RECEIVE time: the wall-clock instant capture took the record from
              Kafka (Unix-epoch ns). Never a source timestamp. Clamped to be non-decreasing
              in write order, so a clock step cannot break receive order (R4 conformance).
publish_time  the TRANSPORT ingest time: the envelope's ingest_timestamp_ns, the instant the
              bridge accepted the message. It is neither a source observation time nor a
              robot-side publication time.
source time   inside the payload, unrewritten (Header.stamp, a transform stamp, a JSON field),
              including a zero stamp. TelemetryEnvelope.source_timestamp_ns is the bridge's
              verbatim copy of it; it is not written to the recording separately and never
              substituted by another time.
sequence      MCAP Message.sequence = TelemetryEnvelope.sequence_number + 1. MCAP reserves 0
              for "no sequence" and the bridge's counter starts at 0. It increases within every
              channel; it is a transport arrival counter across all channels, with gaps in any one
              channel.
```

- **`publish_time` (clarifies §29.5 R4 and §29.14).** R4 defines `publish_time` as the upstream
  publication time "when the transport provides one, otherwise equal to `log_time`", and §29.14 /
  §29.20 write "upstream publication (bridge ingest) time". A raw ROS 2 subscription exposes no
  publisher timestamp, and a DDS source timestamp, where one exists, is the publisher's own
  wall clock (replay wall-clock for a replay) and is not carried by the envelope. The only
  upstream-of-capture time SceneOps's transport observes is the bridge's acceptance time, and R4
  requires a transport-level timing fact to survive into the recording. A7 therefore reads those
  passages as: on the streaming path `publish_time` is the **transport ingest time**, under that
  name. It is never described as a publication or observation time. A batch writer keeps
  `publish_time = log_time` (§29.5). The three facts stay distinct: source observation time
  (payload), transport ingest time (`publish_time`), receive time (`log_time`). A build
  configured from `mcap_publish_time` depends on the acquisition, like one from `mcap_log_time`,
  and is outside I-35 (§29.12).
- A recording's `started_at` / `ended_at` and `capture.source_clock = "mcap_log_time"`
  keep the §29.5 R5 meaning: the recording clock. For a streamed recording it is
  wall-clock receive time. Anything a build derives from it is outside I-35 (§29.12).
- Cross-channel arrival order (Kafka order, write order, the global sequence) is acquisition
  evidence. It is never canonical temporal identity (I-34).
- Transport redelivery (same sequence number and payload as the last accepted record) is dropped.
  Source-level duplicates are separate occurrences with separate sequence numbers: neither the
  bridge nor capture ever deduplicates them.
- A source's own per-topic counters cannot cross a ROS 2 topic. The replay sink drops the event's
  `sequence`; on the streaming path only the transport sequence exists. Equivalence therefore
  compares per-channel sequence *order*, not values (§32.9).
- R11: `/mission/status` events carry `source_timestamp_ns` on the source timeline. The replay
  sink replays the acquisition tool's events, which sit at the unit's first and last source
  times. No replay path writes replay wall-clock or pacing time into any timestamp.
  `can_replay_node.py`, the last writer that did, is removed.

### 32.3 Channel registry

The bridge and capture share one declarative registry, `sceneops_core.streaming.channels`
(`ChannelSpec`: topic, ROS 2 type, source-timestamp rule, latched, optional history depth).
It holds transport facts only, never a modality, sensor, Scene, Episode or dataset format, so
source-format-specific logic stays outside core. Built-in defaults: the five vehicle / mission
telemetry channels, `/tf` and `/tf_static`. Sensor channels (camera, `CameraInfo`, lidar, ...) are
deployment configuration: channel-set JSON files passed to both processes (`--channels-file`).
There is no dynamic topic discovery.

- Timestamp rules only *locate* a timestamp the message already carries: `header`,
  `transform_header` (first transform's stamp), `json_field`. A missing or malformed timestamp
  fails loudly at the bridge.
- **Zero stamps.** A zero source timestamp (an unstamped header, typical of `/tf_static`) is the
  source's own value. `TelemetryEnvelope.source_timestamp_ns` accepts 0 (it was required positive;
  the loosening is backward compatible), and a channel opts in with `allow_zero_stamp` (built-in:
  `/tf_static` only). The zero stays 0 in the payload and in the envelope and is never replaced
  by the ingest time or any other clock; no source timestamp is manufactured. On any other channel
  a zero stamp is a missing observation time and fails loudly. Static transforms' stamps are not
  read by canonicalization, so a recording with an unstamped `/tf_static` builds the same
  calibrations as a stamped one. The `CALLBACK` rule (bridge node-clock time) is removed:
  it could substitute receive time for source time.
- `/tf_static` is latched (transient-local), so static transforms published before the bridge or
  capture started are still received (R9).
- Subscription history is keep-all by default. A source can emit bursts far larger than any
  fixed depth (a sensor frame's messages share one instant), and DDS drops the oldest sample of a
  full keep-last history without any signal. A depth is an explicit memory bound that accepts
  silent loss.

### 32.4 Payload exactness (R2) and DDS alignment padding

The bridge's subscriptions are raw: it forwards serialized CDR and deserializes only to read
the source timestamp. DDS pads a small serialized sample to a 4-byte multiple, so a raw take
can end in 1–3 zero bytes the publisher never wrote. The first real vertical showed it: 119 B
published, 120 B received; 73 → 76; 365 → 368 (9 channels' payload multisets differed; fragmented
large samples are not padded). The bridge removes the padding only with proof: it trims `raw` by
the excess over the canonical re-serialization's *length* when that excess is 1–3 zero bytes.
Only the length is used, never the re-serialization's bytes (alignment padding inside a CDR message
is indeterminate in a re-serialization), and the forwarded bytes are always the received ones.
Anything else is forwarded unchanged. The recording's payload bytes are the publisher's bytes.

### 32.5 Capture

- **Writer.** Capture writes MCAP with the official `mcap` writer in the ROS 2 profile.
  rosbag2's MCAP plugin always writes `sequence = 0` and takes one receive and one send
  timestamp, so it cannot record the sequence. Schema text is generated from the `.msg` files of
  the installed ROS 2 distribution (type text plus dependencies, separator and `MSG: pkg/Name`
  sections: the rosbag2 format); tests decode `rclpy`-serialized payloads with it through an
  independent MCAP ROS 2 decoder. The directory layout and the finalize protocol are unchanged:
  consume → write `.partial` → close/fsync → validate by read-back → atomic rename → commit offsets.
- **Lifecycle.** The bridge publishes `RUN_START` at startup and `RUN_END` after its last record
  (default on); `--exit-after-idle-seconds` ends a finite source. Capture ends at `RUN_END`
  (`--until-run-end`), `--max-messages`, or an idle timeout, whichever comes first. Channels are
  registered in the MCAP when first written, so asynchronous channel arrival needs no
  coordination. One run produces one MCAP.
- **Restart and failure (current behavior).** Offsets are committed only after finalization.
  A death before finalize discards `.partial` and rebuilds from Kafka. A death after finalize and
  before commit converges on re-run if the recorded messages match (topic, schema, publish time,
  sequence, payload; `log_time` excluded, because each attempt stamps its own receive time): the
  existing file stays the recording of record. Different content under one run id fails with
  `FinalBagExistsError` and commits nothing. A bridge killed without `RUN_END` leaves only an
  idle timeout, which cannot tell a complete run from a truncated one. Every message the bridge
  receives takes a sequence number before anything can fail, so a message it drops leaves a gap,
  and the gap fails the capture. In-flight state of the continuous router is
  lost on restart; durable recovery stays DEFERRED (§26).
- **Hand-off.** Capture → publish → register remain explicit steps, with the manifest-last
  protocol unchanged. Automating them stays optional and deferred.

### 32.6 Replay sink

The sink is `tools/dataset-acquisition --replay`, in a ROS 2 image built from the same project and
lock (target `replay`). It consumes the same tool-local acquisition-event stream as the batch MCAP
sink and publishes each payload as raw CDR bytes, so both sinks emit identical bytes. It has no
SceneOps dependency (I-36, enforced at source, lockfile and both images), no credentials and no
Kafka address; it meets the platform only over DDS. Pacing schedules `source_time_ns`, scaled by
`--rate` (`0` = unpaced). It refuses to publish before every topic has a matched subscriber, uses
reliable keep-all delivery with `/tf_static` latched, and fails unless every sample is acknowledged
after the last message.

**Amendment note (reference streaming acceptance, after A9).** The decision above stands as
written for the source adapter → replay sink design of step 9, and as history: the sink
consumed the nuScenes adapter's events (`nuscenes --replay`) and the original acceptance
replayed a source scene. That runtime path has since been removed: the replay sink now has
one source, a finalized MCAP, and the current streaming acceptance replays the *locked L1
reference MCAP* of a reference-corpus fixture (`reference replay`, which reads no source
dataset) through the same sink. The sink, its payload-exactness, pacing and delivery rules
and I-47 are unchanged; only the origin of the events differs. The consequence for the
acceptance is stated in §32.9.

### 32.7 Q3 decided: Kafka transport of large sensor messages

Stock broker, producer and consumer limits (~1 MB message size) are sufficient for the measured
payloads, so no limit was changed. Measured scope: nuScenes v1.0-mini on one host. Largest payloads:
lidar `PointCloud2` 696,320 B over all 3,935 sweeps and samples; camera JPEG 298,656 B over 2,342
front-camera frames; the largest message in the replayed scene was 695,849 B. A 1.5 MB payload fails
loudly at publish (`Message size too large`), and the bridge counts and logs it. A source with
messages over ~1 MB needs the producer, topic and broker limits raised together; that is not
configured and not measured. The replay → bridge → Kafka → capture path carried all 8,897 messages
of a scene (about 356 MB, 20 channels) with equal per-channel counts at replay rates 1×, 2×, 8× and
unpaced; a keep-last depth of 100 lost bursty channels (§32.3). These measurements describe this
workload and are not a throughput limit.

### 32.8 Boundaries kept

The streaming path writes no PostgreSQL or ArtifactStore state except through the Recording Publisher
and `REGISTER_ROBOT_RUN`. The targeted vertical uses containers for bulk data (the shared recordings
volume, DDS, Kafka) and FastAPI for platform operations; it needs no host `uv`, PostgreSQL access,
MinIO credentials or worker CLI.

### 32.9 Equivalence: executable forms and result

```text
recording level (§29.12)  sceneops_integrations.recording.compare_recordings / `compare`:
                          per channel (schema name, schema encoding, message encoding), the message
                          multiset by sha256(payload) with duplicates counted, and per-channel
                          sequence order where both recordings are sequenced. Ignores log_time,
                          publish_time, cross-channel order, sequence values, schema formatting.
canonical level (I-35)    semantic_scene_content / semantic_episode_content over every Scene and
                          Episode, matched by unit key; provenance must differ (RobotRun and
                          producer fingerprint), so equality is not vacuous. A negative control
                          removes a Scene and requires rejection.
```

`make e2e-streaming-equivalence` (nuScenes v1.0-mini scene-0061, replay rate 2×): 8,897 messages on
20 channels, replay = bridge = capture = batch counts per channel; the captured recording is
L1-conformant and every channel sequenced; both RobotRuns register; 3 Scenes (606 observations) and
1 Episode (224 observations) are built from each with identical source-timestamp build configurations;
recording equivalence holds; every Scene's and the Episode's semantic content is equal. RobotRun B's
extent is wall-clock receive time, RobotRun A's the source timeline. This is one scene at one rate,
a measurement and not a general proof.

**Amendment note (reference streaming acceptance, after A9).** The result above was
measured when the batch arm was a fresh conversion of a source scene and the streaming arm a
replay of the adapter's events, so the comparison covered two conversions as well as the
transport. `make e2e-streaming-equivalence` now uses one shared acquisition fixture: the locked
reference MCAP is both the batch arm (the persistent reference baseline's RobotRun, read as
it is and verified against the corpus lock) and the replay source of the streaming arm
(replay → ROS 2 → bridge → Kafka → capture → `publish-pending` → `reconcile --apply` →
RobotRun → Scene / Episode, with the baseline's build configuration files). Equivalence is
therefore a transport-preservation test, not a conversion test, and §29.12's relation is
checked over the locked and the captured recording: channels, message types and encodings,
per-channel payload sequences and counts, every `Header.stamp` and the mission event times,
and `/tf_static`; then I-35 over the Scene and Episode. Not compared: container bytes,
capture `log_time`, schema-definition text, cross-channel write order. The negative controls
are minimal perturbations of the loaded data (a dropped message, a 1 ns time shift, a changed
payload checksum), each of which the verifier must report. The run's Kafka records are asserted
as `RUN_START`, `message_count` telemetry records and `RUN_END`; the two lifecycle control
records are why a capture receipt's offset range spans `message_count + 2` offsets (see
`docs/architecture/streaming-transport.md` §16). Measured once on the local stack for the
smoke fixture (`scene-0061`, 8,897 messages, 20 channels, rate 2×, two executions, a persistent
baseline reused unchanged): 1 RobotRun, 1 Scene (606 observations) and 1 Episode (224
observations) per arm, every comparison equal, every negative control detected. That is one
fixture at one rate, not a general proof.

### 32.10 Invariants

```text
I-44  A recording written by capture takes log_time from the recorder's receive clock and
      publish_time from the transport's ingest time (not a publication or observation time).
      Source observation time stays in the payload, a zero stamp included. No component writes one
      of these times into another's field or synthesizes a source-semantic timestamp from
      wall-clock, ingest or replay-pacing time.
I-45  Capture preserves payload bytes exactly as the publisher produced them. The only byte the
      transport path removes is DDS alignment padding, and only on proof (§32.4). Source-level
      duplicates are preserved as separate occurrences.
I-46  No streaming stage loses a message silently. Every loss is a counted, logged failure, or a
      sequence gap that fails the capture. The vertical checks replay = bridge = capture counts per
      channel.
I-47  The replay sink publishes the acquisition events' payload bytes unchanged and depends on no
      SceneOps package. Batch and streaming acquisition of one source produce semantically equivalent
      recordings.
```

Backing tests: `ros2/nodes/tests/test_streaming_bridge_node.py` (including zero-stamped `/tf_static`),
`apps/worker/tests/scenes/test_recording_scene_builder.py` (unstamped static calibration),
`ros2/capture/tests/` (writer, message
definitions, `run_capture`, crash boundaries C and D, router, real-Kafka and sensor-payload integration),
`packages/sceneops-core/tests/test_streaming_channels.py`,
`packages/sceneops-integrations/tests/test_recording_equivalence.py`,
`tools/dataset-acquisition/tests/test_ros2_replay.py` and the import-boundary test, and
`make e2e-streaming-equivalence`.

### 32.11 Sections affected

§29.5's "HEAD 1406cb2" column and the §29.14 gap list describe the audited state before A7; the rules
above supersede them. The phrase "upstream publication (time)" in §29.5 R4, §29.14 and §29.20 is read as
the transport ingest time on the streaming path (§32.2). §29.20's `acquisition`, `capture` and `clock` rows are implemented. Q3 (§29.21) is
decided (§32.7). `docs/architecture/streaming-transport.md` describes the resulting system.

### 32.12 Remaining work (not decided here)

```text
step 10  derived workflow cleanup (labels, SampleView, detection, readiness) is untouched.
step 11  E2E / clean-room consolidation: scripts that reference the removed CAN replay node
         (e2e_robot_learning.sh, e2e_robot_run_learning.sh, canonical_bootstrap.sh) are already
         marked unavailable until then.
open     automatic capture → publish hand-off; durable CaptureSession recovery; Kafka sizes beyond
         ~1 MB; a bridge QoS depth that bounds memory without silent loss.
```


---

## 33. Amendment A8: derived L3 layer (step 10)

### 33.0 Scope and result

Steps 7–8 made Scenes and Episodes source-faithful. What was left of the old
nuScenes-first architecture sat above them: workflows that read ground truth and
keyframes *out of the canonical Scene*, a dataset index that only those workflows
consumed, string defaults that named one source, and derived artifacts whose
identity was a random id. A8 resolves that layer. L2 is unchanged: no Scene or
Episode manifest, record, identity or fingerprint changes.

```text
Scene   --labels-->  LabelSet revision  --+
Scene   --policy-->  SceneSampleView  <---+ (pins Scene revision + label revisions)
                          |
                    ScenarioSet revision (pins views)
                          |
                    detection prediction revision (pins views, ScenarioSet, config)
                          |
                    evaluation (pins prediction revision + label revision)

Episode --policy-->  AlignedEpisode revision --> learning export (explicit config)
```

Every arrow is a pin, not a lookup (§33.1).

Classification of the derived workflows audited at HEAD 72c5aab:

| Workflow / code | Class | Outcome |
|---|---|---|
| keyframe sample projection (`scenes/keyframes.py`), `scenes/selection.py` | legacy nuScenes / sample-first | REMOVED; replaced by `SceneSampleView` (§33.3) |
| ground truth read from `SceneManifest.annotations` by selection, curation, evaluation, mock backend | legacy | REMOVED from every consumer; labels are separate (§33.2) |
| derived dataset index (`DatasetManifest`, `BUILD_DATASET_MANIFEST`, `BUILD_SCENE_INDEX`, `DatasetVersion.manifest_uri`) | dead after the consumers above are replaced | REMOVED |
| ONNX "backend" copying ground truth into predictions | dead / misleading | REMOVED |
| frustum lifting from a nuScenes `pcd.bin` payload, hard-coded `LIDAR_TOP`, `CAM_FRONT`, nuScenes category prefixes | legacy nuScenes assumption | REPLACED (§33.5) |
| platform-wide default DatasetVersion `nuscenes` / `v1.0-mini` | legacy default | REMOVED |
| reserved JobTypes (`COMPARE_SCENES`, `AUTO_LABEL_*`, `EXPORT_*`) and their params/results | dead, superseded by labels | REMOVED |
| scenario mining profiles (`detection_ready`, ...) | legacy GT-from-Scene | REPLACED by explicit criteria (§33.4) |
| `align_episode`, validation, profiling, `ExportLearningData`, `CurateEpisodes` | valid L3 primitives | KEPT; identity completed (§33.6) |
| robot telemetry projections (`INGEST_ROBOT_STATES`, robot analytics snapshot) | valid L3 projection, naming debt | KEPT; Step 11 |
| LeRobot adapter | export interoperability, downstream only | KEPT unchanged (§33.7) |
| model-specific logic (GroundingDINO phrase to category mapping, DBSCAN lifting) | model-specific, stays downstream | KEPT in the inference server and backend |

### 33.1 Derived revisions: pins, identity, write-once

A derived manifest is an immutable *revision*:

```text
canonical bytes (§8.3 encoding)  ->  checksum  ->  write-once key  ->  ArtifactRecord
revision identity  = checksum of its canonical bytes
ArtifactRecord id  = sha256(canonical{prefix, logical id, checksum})[:32]   (never random)
key                = .../manifest-<checksum hex>.json        (same bytes: no-op; other bytes: conflict)
read               = always through the pinned checksum, parsed strictly
```

Label sets, sample views, ScenarioSets, prediction manifests and aligned episodes
share this shape (`sceneops_core.common.derived_ids`,
`sceneops_worker.derived`). A retry that rebuilds identical bytes registers the
*same* ArtifactRecord (`ArtifactRecordStore.register` is idempotent for an
identical record and fails loudly for a conflicting one); a changed input is a new
revision, never an overwrite. A consumer resolves a pin by verifying that the
ArtifactRecord it names exists, has the right kind and owner and carries exactly
the pinned checksum, and that the bytes hash to it. A record written before pinning
existed (a `scenario_sets` row or inference run with no checksum) is refused
(`LegacyDerivedRecordError`); it is rebuilt, never guessed at.

A manifest holds no storage location of itself, execution context, timestamp or
DatasetVersion membership it did not need; where it lives is the ArtifactRecord's
concern.

### 33.2 Q1 decision: labels are separate, lineage-bearing data

**Decision.** A `LabelSetManifest` (`sceneops.label_set/v1`) is its own artifact
type:

```text
LabelSetManifest {
  label_set_id
  provenance { kind: human|external|model, producer, producer_version?, model_id?, model_version? }
  coverage   [ObservationAnchor]     every anchor the set annotated, with or without objects
  labels     [Box3DLabel { label_id, anchor, category, instance_id?, box{frame, center, size_wlh, rotation_wxyz}, attributes }]
}
ObservationAnchor = (robot_run_id, channel, source_clock, timestamp_ns)
```

- Labels are post-acquisition data. They are not part of RobotRun provenance and
  never enter a Scene, an Episode or an L1 recording. `IMPORT_LABELS` validates and
  canonicalizes an adapter-produced document and registers a revision; it reads
  the document from the raw-source store (an external input), checks that every
  anchored RobotRun is registered, and touches nothing canonical.
- **Anchors** are source-semantic and stable across re-canonicalization and
  DatasetVersions: the observation's channel (the recorded topic), its canonical
  timestamp and that timestamp's clock. This refines the §29.15 recommendation
  (RobotRun, topic, message occurrence index): a Scene manifest does not carry the
  occurrence index (the payload id hashes it), whereas channel, clock and canonical
  timestamp are in every Scene and Episode. An anchor matching more than one
  observation of one Scene fails the build (`AmbiguousAnchorError`).
- **Coverage** makes "annotated as empty" distinguishable from "never annotated".
  An evaluation scores only covered samples, so a true negative is never confused
  with a missing label.
- **Revisions.** A label set id names a logical set; each serialized document is
  one revision. Consumers always pin a revision, so a set can gain revisions
  without changing what an earlier evaluation read. No mutable "current" pointer
  exists.
- **Provenance.** `human`, `external` and `model` are provenance, not trust
  levels. Model-generated labels must name the model revision. Pseudo-labels from
  a prediction revision are therefore ordinary label sets (this is why the
  reserved `AUTO_LABEL_*` job types were removed).
- **Adapters stay outside core.** Format knowledge (nuScenes annotation tables) lives
  in `tools/dataset-acquisition` (`nuscenes-labels`), which emits the document shape
  as plain JSON and imports no SceneOps package (I-36). `SceneManifest` still
  *allows* an `annotations` array (schema unchanged, §33.11) but the recording
  builder never emits it and no derived workflow reads it.

### 33.3 SceneSampleView

`SceneSampleViewManifest` (`sceneops.scene_sample_view/v1`) derives, per Scene
revision, an explicit synchronization / association / sampling of its observations:

```text
Scene revision + SampleViewPolicy + [LabelSet revisions]  ->  SceneSampleViewManifest

SampleViewPolicy {
  anchor   { channel, stride }               sample instants: every stride-th observation of one channel
  members  [{ channel, association: nearest|previous, tolerance_ns, required }]
  pose     { parent_frame_id, child_frame_id, association, tolerance_ns, required }?
}
```

- The Scene stays asynchronous. A sample is a chosen instant plus the
  observations, pose and labels associated with it.
- **No cross-clock association** (I-38 extended): every associated channel and pose
  must be on the anchor channel's clock, otherwise the build fails; nothing is
  converted, interpolated or resampled. Nearest ties go to the earlier timestamp
  (then observation id).
- A required member or pose that cannot be associated drops the anchor, and the
  drop is recorded with its reason (`dropped`), never silent.
- The view stores references and time deltas only (observation ids, pose ids, label
  ids). Payloads, calibrations and pose values stay in the pinned Scene revision.
- **Labels attach through the observations a sample holds.** An observation can be
  held by several samples, so each labelled or covered observation is *owned* by the
  sample whose anchor instant is nearest to it (earlier on a tie): a label is
  counted once. `label_stats` accounts for covered observations, attached labels
  and labels anchored on observations no sample holds.
- The policy, the pinned Scene revision, the pinned label revisions and
  `builder_semantics_version` are inside the manifest, so identical inputs rebuild
  identical bytes. The view is DatasetVersion-scoped only through the Scene
  revision it pins.
- `BUILD_SCENE_SAMPLE_VIEWS` publishes one revision per Scene and reports skipped
  Scenes (`scene_lacks_anchor_channel`, `no_samples`) with reasons.

### 33.4 ScenarioSet

A `ScenarioSetManifest` (`sceneops.scenario_set/v1`) is a curated, ordered
selection over *sample views*. Each member pins a SceneSampleView revision and names
the selected sample ids, label count, channels and Scene readiness (derived from
validation runs of the exact pinned Scene revision). The curation parameters are
explicit data in the manifest: the pinned label set revision, label count bounds,
required channels, readiness values, sort and limit. Label criteria without a label
set are rejected, never silently unfiltered, and every input view must pin the same
revision of that label set. Mining profiles that encoded ground-truth-from-Scene
(`detection_ready`, `dense_gt`, ...) are replaced by those explicit criteria.
`ScenarioSetRecord` projects exactly one revision (`manifest_artifact_id`,
`manifest_checksum`); a set id is immutable once written. The speculative
`ScenarioRecord` / predicate / curation-config types, which had no consumer, were
removed.

### 33.5 Inference and evaluation

- **Input.** `PREDICT_DETECTION` takes either a ScenarioSet (its pinned member views
  and selected samples) or an explicit list of pinned views, never a DatasetVersion
  scan. The DatasetVersion is scope only, and each view must belong to it. Channel
  names are explicit parameters (`camera_channel` required; `lidar_channel`
  optional, `None` disables lifting). Scene readiness gates run over exactly the
  pinned Scene revisions.
- **Output.** A run publishes a `DetectionPredictionManifest`
  (`sceneops.detection_prediction_manifest/v1`): the explicit configuration, the
  pinned view revisions and sample ids that ran, the ScenarioSet revision, and one
  checksum-pinned shard per sample. A shard path includes the Scene id because a
  sample id is unique only within its view. The revision is published write-once;
  `InferenceRunRecord.prediction_manifest_checksum` pins it.
- **Lidar.** A canonical `ros2_message` lidar payload is decoded by its declared
  media type. `application/x.ros2-cdr.sensor_msgs.msg.pointcloud2` is read directly
  from its CDR layout (honouring field offsets, endianness and `row_step`); an
  unknown media type is a recorded failed lift, never a guess. The lifted box is
  expressed in the associated pose's parent frame (or the calibration's ego frame
  without a pose) and carries that `frame_id`; rotation is composed with the pose.
- **Evaluation.** `EVALUATE_DETECTION` always pins a label set revision and resolves
  the prediction revision (explicitly pinned, or the one the run recorded). Every
  view the predictions came from must pin that label revision. The evaluation
  manifest records `inputs {prediction, label_set, sample_views, scenario_set}`. A
  predicted sample is scored only if the label set covers it; uncovered samples are
  skipped, or fail the evaluation under `missing_gt_policy=fail`. A prediction and a
  label in different frames fail loudly: no frame transform is applied. A prediction
  is scored only if it is a localized 3-D box (`frame_id` present, lift not failed).
  `categories` is an exact allowlist; nuScenes category prefixes are no longer
  assumed.
- The mock backend is a deterministic test double: it perturbs the labels attached to
  a sample, seeded by run and sample. Real backends never receive labels.

### 33.6 AlignedEpisode

`align_episode` was already structural (v2). A8 completes its identity:

- The alignment *recipe* key includes the clock the alignment ran on, together with
  the full config hash and the semantics version
  (`alignment_key(config, semantics_version, source_clock)`). Two results of one
  config on two clocks no longer share a URI.
- An aligned artifact is written write-once at its key: identical bytes are a retry,
  different bytes are a conflict. Its ArtifactRecord id is derived from the episode
  id and the artifact checksum, not random. Validation, profile and export records
  are deterministic the same way.
- The DatasetVersion scope of an aligned artifact is its Episode's (and, for
  validation and profiling, the aligned artifact's), never a configured default.
  Params naming a different scope are rejected.
- `ALIGNED_EPISODE_BUILDING` (align, then validate with a quality gate, then optional
  profile) is a pipeline; the canonical Episode is never rewritten.

### 33.7 Learning export

`EXPORT_LEARNING_DATA` takes explicit, revision-pinned aligned artifacts and an
explicit `LearningDataExportConfig`; its export id is a content hash of the sorted
aligned checksums, the config and the table schema version, and its ArtifactRecords
are now deterministic (§33.1). LeRobot and other external formats are interoperability
*outputs* produced by the isolated runtime from a pinned export manifest; they define
no core type, and there is no ingest direction (A5, §30.7). No change to the LeRobot
adapter was needed.

### 33.8 Removed and changed

```text
removed   worker      scenes/keyframes.py, scenes/selection.py, scenes/indexing.py,
                      datasets/artifacts.py (DatasetArtifactStore), BUILD_DATASET_MANIFEST,
                      BUILD_SCENE_INDEX, ONNX backend, inference/constants.py,
                      scenario resolver package, unused RunArtifactStore sections
          core        DatasetManifest / DatasetSceneIndexEntry / DatasetSplit /
                      DatasetIngestMode / DatasetManifestStatus, the dataset Protocols,
                      DefaultDatasetSettings, constants/sensors.py (nuScenes channel names),
                      reserved JobTypes and params/results, ScenarioRecord / predicates /
                      curation config, ArtifactKinds scene_index and dataset_manifest,
                      InferenceBackendType / ModelBackend ONNX_RUNTIME
          api         POST /scenario-sets (a ScenarioSet exists only as a mined revision),
                      ground-truth / selectability fields of scene and dataset quality,
                      manifest_uri on DatasetVersion requests, default DatasetVersion on
                      job and pipeline creation
changed   db          migration e5a7c1d9b3f4: scenario_sets pins a manifest revision,
                      inference_runs pins a prediction revision, dataset_versions.manifest_uri
                      and *.dataset_manifest_uri dropped
          jobs        IMPORT_LABELS, BUILD_SCENE_SAMPLE_VIEWS added; MINE_SCENARIOS,
                      PREDICT_DETECTION, EVALUATE_DETECTION params reworked (§33.4, §33.5)
          pipelines   ALIGNED_EPISODE_BUILDING added; SCENARIO_CURATION and
                      DETECTION_EVALUATION re-pointed to pinned inputs
          tooling     dataset-acquisition gains nuscenes-labels (label documents)
```

**Workflows.** Of the logical L3 workflows, `ALIGNED_EPISODE_BUILDING`,
`SCENARIO_CURATION` and `DETECTION_EVALUATION` (inference + evaluation) are
pipelines because they sequence several jobs with a gate or a ref hand-off. Label
import, sample view building and learning export are single stages and stay Jobs; the
final naming and command surface is Step 11.

### 33.9 Invariants

```text
I-48  A derived manifest is an immutable revision: its identity is the checksum of its canonical
      bytes, its key is write-once, and every consumer resolves it only through that pin. A record
      that does not pin a revision is refused, never resolved to "the latest".
I-49  Derived ArtifactRecord ids are deterministic functions of (logical id, checksum). A retry of
      identical work registers the same record; a conflicting duplicate fails loudly.
I-50  Labels never enter a Scene, an Episode or a recording, and are never part of RobotRun
      provenance. A label references canonical observations only through an ObservationAnchor, and
      a label set declares its coverage.
I-51  Synchronization, nearest/previous association, sampling and label attachment live in
      SceneSampleView, not in the Scene. Association never converts between clocks, and an
      observation's labels are counted once.
I-52  A ScenarioSet, a prediction manifest and an evaluation pin the exact revisions they consumed
      (views, label set, ScenarioSet, prediction manifest). Evaluation scores only samples its label
      set covers, and compares only values in the same frame.
I-53  Channel names, frame names, category taxonomies and lidar layouts are explicit configuration
      or declared payload metadata. No derived workflow carries a source's vocabulary as a default.
I-54  The alignment recipe key covers the full config, the semantics version and the alignment
      clock; an aligned artifact is write-once at its key.
I-55  A DatasetVersion is never defaulted. A derived artifact's DatasetVersion scope comes from the
      canonical unit or pinned input it derives from.
```

Backing tests: `packages/sceneops-core/tests/test_label_set.py`, `test_scene_sample_view.py`,
`test_derived_manifests.py`, `test_alignment_execution_identity.py`;
`apps/worker/tests/derived/` (import, sample views, curation, detection vertical),
`apps/worker/tests/inference/detection/` (PointCloud2 decoder, frustum lifting),
`apps/worker/tests/jobs/test_align_episode_handler.py`,
`apps/worker/tests/episodes/test_episode_artifacts.py`;
`tools/dataset-acquisition/tests/test_labels.py`; real PostgreSQL + MinIO:
`apps/worker/tests/derived/test_derived_vertical_integration.py`,
`packages/sceneops-db/tests/test_derived_run_repositories.py`.

Real-data verticals (nuScenes v1.0-mini `scene-0061`, live stack, API only):

```text
make e2e-perception            exit 0. 3 Scenes (annotation count 0), 4699 labels over 39 covered
                               samples imported as one external-provenance revision (re-import
                               converges, Scenes unchanged), 3 views / 219 samples / 5 dropped
                               anchors (lidar within 25 ms, pose within 5 ms), ScenarioSet with 2
                               selected Scenes, mock backend: 9 samples, 880 predictions, 875 tp /
                               5 fp / 170 fn against the pinned label revision, predict retry under
                               the same run id converges on the same prediction checksum, a real
                               lidar payload (34688 points) decodes to the source .pcd.bin points.
                               The mock backend perturbs the labels, so the metrics prove wiring
                               and pinning, not model quality. GroundingDINO was not run.
make e2e-episode-alignment     exit 0. 1 canonical Episode, aligned_episode_building (98 steps)
                               validated and profiled, Episode unchanged, same recipe on the same
                               revision gives the same aligned artifact id and checksum, learning
                               export (98 steps, 784 signals) reproduces the same export id and
                               manifest record.
```

### 33.10 Sections affected

§29.15 and the Q1 row of §29.21 are decided here. The step-10 row of §29.19 is
implemented by A8 (the DatasetVersion fields `manifest_uri` removed; `required_channels`
and DatasetVersion `metadata` stay, §33.11). The §29.16 table row for `manifest_uri` is
implemented. §13.11 ("Scene alignment is optional and derived") is realized as
SceneSampleView rather than as a generic `AlignedScene`; a generic `AlignedScene`
remains a §26 option. §32.12's step-10 line is closed.

### 33.11 Remaining work (not decided here)

```text
step 11   E2E / clean-room consolidation and the canonical baseline regeneration;
          final workflow naming and command surface; the e2e scripts still marked
          unavailable (episode building / curation, robot learning, canonical bootstrap).
deferred  SceneManifest v1 still *allows* an `annotations` array and SceneRecord carries
          annotation_count / keyframe_count. They are L2 schema; removing them is a
          Scene manifest version bump, deliberately not done here (L2 stays frozen) and
          best done with the step-11 baseline regeneration. Nothing derived reads them.
          DatasetVersion.required_channels and DatasetVersion/Dataset metadata; the
          dataset-level workflow configuration they carry belongs with step 11.
          Robot telemetry projection naming (`datasets/ingestion/rosbag_raw_log.py`,
          `raw_source` settings named after nuScenes).
          Pose interpolation in SceneSampleView (nearest / previous only), cross-frame
          transforms in evaluation, and label sets for Episodes (no consumer yet).
          Unused `onnx` / `onnxruntime` dependencies of the worker (uv.lock is not
          touched here).
```

---

## 34. Amendment A9: final workflow surface and ADR closure (step 11)

### 34.0 Scope and result

A9 implements step 11 of §29.19 and closes ADR-007. It consolidates SceneOps into
the final workflow surface, deletes the pipelines, scripts and schema that existed
only because earlier steps deferred cleanup, and records the architecture the
repository now implements. It decides no new ingestion or canonicalization
question: RobotRun, registration, identity, the fingerprint, Scene / Episode
canonicalization and the derived layer are unchanged except where §34.4 and §34.8
name the change.

```text
supported Pipelines   4   recording_scene_building, recording_episode_building,
                          scene_ml_evaluation, episode_learning_data_building
E2E journeys          5   e2e-batch-canonical, e2e-streaming-equivalence, e2e-scene-ml,
                          e2e-episode-learning, e2e-cleanroom
baseline              canonical-bootstrap / canonical-verify: L1/L2 only, not a Pipeline
```

### 34.1 Layers (final)

```text
L0  transport      ROS 2 topics -> Kafka -> capture. Non-canonical; bounded replay.
L1  RobotRun       immutable recording (MCAP) + RobotRunManifest. Created only by
                   REGISTER_ROBOT_RUN from a manifest the DB-free publisher wrote.
L2  Scene/Episode  canonical, source-faithful units built from one RobotRun by
                   RECORDING_SCENE_BUILDING / RECORDING_EPISODE_BUILDING; checksum-pinned
                   manifests; DatasetVersion membership.
L3  derived        LabelSet, SceneSampleView, ScenarioSet, prediction, evaluation,
                   AlignedEpisode, LearningDataExport. Immutable revisions that pin the
                   exact revisions they consumed (I-48). Never part of L1 or L2.
```

```text
Acquisition -> RobotRun
RobotRun    -> Scene | Episode
Scene       -> Labels / SampleView / ScenarioSet -> Inference / Evaluation
Episode     -> AlignedEpisode -> LearningDataExport
```

### 34.2 The four Pipelines

A Pipeline exists only where multi-stage orchestration, retry and lineage justify
it (I-56). Task chains and the REFs that connect them:

| Pipeline | Tasks | REF hand-offs |
| --- | --- | --- |
| `RECORDING_SCENE_BUILDING` | `build_recording_scenes` → `register_scenes` → `validate_scene` → `profile_scene` (optional) | `manifest_artifact_ids`, `scene_ids` |
| `RECORDING_EPISODE_BUILDING` | `build_recording_episodes` → `register_episodes` → `validate_episode` → `profile_episode` (optional) | `manifest_artifact_ids`, `episode_ids` |
| `SCENE_ML_EVALUATION` | `build_scene_sample_views` → `mine_scenarios` → `score_scenario_readiness`; `predict_detection` → `evaluate_detection` | `views`; `scenario_set_id` (+ checksum); `inference_run_id` + `prediction_manifest_checksum` |
| `EPISODE_LEARNING_DATA_BUILDING` | `align_episode` → `export_learning_data` | `export_inputs` (episode id, aligned artifact id, checksum) |

Each L3 stage takes its pinned input from the upstream stage's REF
(`MineScenarios` ← `views`, `PredictDetection` ← `scenario_set_id`,
`ExportLearningData` ← `export_inputs`), and a caller-supplied pin is never
overridden by a REF. Policy, channels, label sets, categories, the alignment
config and the model backend are explicit task params; no stage defaults a source
vocabulary (I-53). `EXPORT_LEARNING_DATA` validates every aligned input, so
`EPISODE_LEARNING_DATA_BUILDING` has no separate validation stage; the
validate / profile jobs of an aligned revision are atomic jobs.

Removed: `SCENARIO_CURATION` and `DETECTION_EVALUATION` (their stages are the
curation and prediction / evaluation stages of `SCENE_ML_EVALUATION`) and
`ALIGNED_EPISODE_BUILDING` (alignment is the first stage of
`EPISODE_LEARNING_DATA_BUILDING`). The pipeline availability flags
(`supported`, `implemented`, `experimental`) and the API's `include_experimental`
listing option are removed: every defined pipeline is supported.

Airflow: one DAG per pipeline type (`airflow/dags/sceneops_pipelines.py`, DAG id
`<pipeline_dag_prefix>_<type>`). The API picks the DAG from the run's pipeline
type (`PipelineExecutionBackend.dispatch_pipeline(run_id, pipeline_type)`). The
DAG file mirrors the definitions' task ids statically and a unit test fails when
they drift. The `job_dag_id` setting, which named no DAG, is removed.

### 34.3 Atomic Jobs

Reusable single operations stay Jobs (`POST /jobs`) and share handlers with the
pipeline stages that use them:

```text
REGISTER_ROBOT_RUN   IMPORT_LABELS   BUILD_SCENE_SAMPLE_VIEWS   MINE_SCENARIOS /
SCORE_SCENARIO_READINESS   PREDICT_DETECTION   EVALUATE_DETECTION   ALIGN_EPISODE
VALIDATE_ALIGNED_EPISODE / PROFILE_ALIGNED_EPISODE   EXPORT_LEARNING_DATA
CURATE_EPISODES   EXPORT_ANALYTICS_SNAPSHOT   INGEST_ROBOT_STATES /
EXPORT_ROBOT_ANALYTICS_SNAPSHOT   VALIDATE_* / PROFILE_*   BUILD_RECORDING_* / REGISTER_*
```

The conceptual names `PREDICT` and `EVALUATE` are the `JobType`s
`PREDICT_DETECTION` and `EVALUATE_DETECTION`, and `CURATE_SCENARIOS` is the
`MINE_SCENARIOS` + `SCORE_SCENARIO_READINESS` pair. They are not renamed: the
handlers are detection-specific (3-D box labels, camera + lidar lifting), and a
generic name would assert a model-task abstraction that has no second use case
(§3.3, architecture rule 5). `CURATE_EPISODES` stays because `SceneOpsDataset`
reads its manifests to select episodes.

### 34.4 Changed job contract: `ALIGN_EPISODE`

A Pipeline has no fan-out, and `EPISODE_LEARNING_DATA_BUILDING` must accept several
pinned Episodes. `ALIGN_EPISODE` therefore takes `episodes`, a list of
`{episode_id, source_artifact_id?, source_manifest_sha256?}` (each pin is the pair
or nothing), one shared `alignment_config` and an optional `source_context`, and
returns `aligned`: one `{episode_id, aligned_artifact_id, uri, checksum, source
revision, step count}` per Episode, plus `export_inputs` in the exact shape
`EXPORT_LEARNING_DATA` consumes. Every aligned artifact is still write-once at its
deterministic key and its ArtifactRecord id still derives from (episode,
checksum), so a retry converges. The records are registered and committed once at
the end; a failing Episode commits nothing. The API pins each unpinned Episode to
its current revision at job creation (§14.4), and the execution key sorts the
Episodes and strips the lineage-only `source_artifact_id` of each, so naming the
same set in another order is one execution. The single-Episode form is removed, not
kept beside the list.

### 34.5 Final E2E surface and test layers

| Journey | Source → result |
| --- | --- |
| `e2e-batch-canonical` | dataset fixture → batch MCAP → RobotRun → Scenes → Episodes |
| `e2e-streaming-equivalence` | one source in batch and ROS 2 → Kafka → capture → both RobotRuns → Scene + Episode equivalence (I-35) |
| `e2e-scene-ml` | Scenes → LabelSet → SampleView → ScenarioSet → inference → evaluation (mock backend) |
| `e2e-episode-learning` | Episodes → AlignedEpisode → LearningDataExport → export verification + LeRobot round trip |
| `e2e-cleanroom` | fresh state → canonical-bootstrap → both L3 journeys → final verification |

| Layer | Responsibility |
| --- | --- |
| unit (`make test`) | pure logic, schemas, state, definitions, REF chaining, execution identity |
| integration (`make test-integration`) | repositories, migrated schema, ArtifactStore, registrars, recording verticals — real PostgreSQL / MinIO |
| infrastructure (`make test-infrastructure`, `-airflow`) | pipeline contracts: retry / dedup / force / convergence / replacement / blocked resumption / failure recovery / concurrent registration, Celery and Airflow execution, MinIO selective reads |
| E2E journey | user journeys through production paths |
| clean room | reconstruct baseline and representative workflows from nothing |
| model-backend acceptance (`make acceptance-grounding-dino`) | the Scene ML journey with the real detector |

Infrastructure behavior is not a user journey (I-57): retry, dedup, force,
replacement, failure recovery and orchestrator checks that earlier scripts ran
inside E2E scripts (`verify-reliability`, `verify-airflow-backend`, the
conflict / replace blocks of the recording E2Es, `e2e-lerobot-container`'s rerun
check, `e2e-batch-acquisition`'s idempotency block) live in `tests/infrastructure`
or inside the journey that owns the user-visible property.

### 34.6 `canonical-bootstrap` and `canonical-verify`

Developer / test orchestration, not a Pipeline (I-58):

```text
dataset fixture -> dataset-acquisition container -> L1 conformance -> publish
 -> POST /robot-runs:register -> RobotRun
 -> recording_scene_building -> recording_episode_building -> verify
```

It is create-or-verify: a registered RobotRun is reused, an unchanged scope
converges, a changed producer or configuration fails loudly at registration, and
recovery is an explicit reset. `canonical-verify` is the read-only check the
bootstrap ends with: RobotRuns registered with pinned recordings; every Scene and
Episode pinned to the checksum of its manifest ArtifactRecord and validated,
profiled and ready at its current revision; DatasetVersion summaries equal to
membership. Identity is `BASELINE_ID` (RobotRun `run-<id>-<scene>`, DatasetVersion
`sceneops-<id>/baseline`). The Scene and Episode build configurations are
`config/baselines/*.json`, used by every journey that builds from the baseline
recording. The v0.0 baseline family and its YAML spec are removed.

### 34.7 Clean room

`e2e-cleanroom` builds images from the current tree, runs `make local-reset`
(fresh containers and PostgreSQL / Redis / MinIO state; `data/raw` preserved),
runs `canonical-bootstrap` and `canonical-verify`, runs `e2e-scene-ml` and
`e2e-episode-learning` on that baseline, and verifies through the API that every
pipeline of the baseline succeeded, no job failed and the derived artifacts exist.
It uses FastAPI for control-plane operations and containers / the ArtifactStore for
bulk data: no direct SQL, no direct MinIO inspection, no host worker CLI, no host
Python. The old fixture bootstrap (`e2e-bootstrap*`, `scripts/e2e/e2e_fixture_bootstrap.py`),
which wrote PostgreSQL and MinIO directly, is removed.

### 34.8 Schema, contract and surface cleanup

```text
SceneManifest     v1 -> v2: no `annotations`, no SceneAnnotation / SceneBox3D (I-60). v1 bytes
                  are refused (UnsupportedSceneManifestVersionError). RECORDING_SCENE_SEMANTICS_VERSION
                  1 -> 2, so a rebuild is a distinct producer and replacing registered Scenes is explicit.
SceneRecord       - annotation_count (and has_ground_truth); profile run records and results lose
                  annotation_count / annotation_summary / category_distribution; the analytics
                  `annotations` table and the keyframes `annotation_count` column are gone.
DatasetVersion    - required_channels, - metadata (and Dataset - metadata): channel requirements are
                  pipeline / job parameters (I-59). `update_scene_inputs`, the DatasetVersion PATCH
                  endpoint (its only fields were these) and the dataset-level default of
                  `require_target_channels` are removed. DatasetInputRef carries the scope only;
                  PipelineTaskInputs.to_context_values (no callers) is removed.
migration         f6c2a8d4e710 drops scenes.annotation_count, dataset_versions.required_channels,
                  dataset_versions.metadata, datasets.metadata. Scenes registered before it pin v1
                  manifests: they are not rewritten or fabricated into v2; reading one fails loudly
                  and the fix is a rebuild with `replace`.
dead contracts    JobDispatcher, PipelineExecutor, PipelineDispatcher, SceneBuilder/Validator/Profiler,
                  ObservationIngestor/Indexer (no implementer), CreateDatasetVersionRequest,
                  GetDataset*/GetSceneRequest (no caller), the ONNX mention of InferenceBackend.
dependencies      onnx, onnxruntime (and their transitive closure) removed from apps/worker and uv.lock.
naming            `datasets/ingestion/rosbag_raw_log.py` / `RosbagAdapter` -> `robots/telemetry.py` /
                  `RecordingTelemetryReader` (a path, not a store); `RawSourceSettings` / `raw_source` /
                  `raw_source_store` -> `InputSourceSettings` / `input_source` / `input_store`
                  (default root `/data/raw`, SCENEOPS_WORKER_INPUT_SOURCE__*); stale `.env` defaults
                  (default dataset, nuscenes-integration URLs, DATASET_ID) removed.
export            ExternalExportConfig.default_task: an explicit task string for episodes whose export
                  carries none (canonical Episodes have no task, §31.2; LeRobot requires one per
                  frame). An episode's own task wins; nothing is inferred.
```

Examined and retained because each is a valid final concept with a consumer:
`SceneRecord.keyframe_count` and `groups` (source-defined groupings, §13.7);
`CURATE_EPISODES` (read by `SceneOpsDataset`); `EXPORT_ANALYTICS_SNAPSHOT`;
`INGEST_ROBOT_STATES` / `EXPORT_ROBOT_ANALYTICS_SNAPSHOT` (derived robot
telemetry projection, §29.7); the v1 single-file learning layout (kept
deliberately as regression coverage, `scalable-learning-data.md`).

### 34.9 Removed

```text
E2E scripts   e2e-recording-scene, e2e-recording-episode, e2e-perception, e2e-episode-alignment,
              e2e-robot-learning, e2e-robot-run-learning, e2e-episode-building, e2e-episode-curation,
              e2e-scene-analytics-export, e2e-batch-acquisition, e2e-interop, e2e-lerobot-container,
              smoke-lerobot-container, verify-reliability, verify-airflow-backend,
              e2e-bootstrap / -core / -interop, canonical_contract.sh, the v0.0 baseline spec
tests         the E2E fixture bootstrap suites, the golden LeRobot round-trip oracle
              (lerobot_roundtrip_golden.py) and the old test_pipeline_contracts_integration.py
commands      every `UNAVAILABLE until step 11` stub
pipelines     SCENARIO_CURATION, DETECTION_EVALUATION, ALIGNED_EPISODE_BUILDING; the availability flags
```

`make check-commands` fails if an advertised command does not exist, the E2E surface
is not exactly the five journeys, or a Makefile / script references a name above.

### 34.10 Invariants

```text
I-56  A Pipeline exists only where multi-stage orchestration, retry and lineage justify it.
      Single operations are Jobs; a stage and its atomic job share one handler.
I-57  The supported Pipeline surface is exactly four and the E2E surface exactly five.
      Retry, dedup, force, replacement, failure recovery and orchestrator behavior are
      infrastructure contracts proven below the journeys, not inside them.
I-58  The canonical baseline is L1/L2 only: RobotRun, Scenes, Episodes, their validation and
      profiles. Anything derived is an L3 workflow run on top of it, never part of it.
I-59  A DatasetVersion is scope and membership only. It carries no channel requirement, no
      source location and no free-form metadata (strengthens §16 and I-55).
I-60  A SceneManifest embeds no label, annotation or box (strengthens I-50): SceneManifest v2
      has no such structure, and bytes of the removed schema fail loudly.
```

### 34.11 Closure

No ingestion or canonicalization architecture decision remains unresolved:
recording publication, registration, identity, replacement, Scene and Episode
canonicalization, acquisition (batch and streaming), their equivalence, and the
derived layer are decided (§1–§33) and implemented, and the workflow, command and
test surface that exposes them is final. **ADR-007 is complete.** What remains is
operational and scaling debt that blocks nothing (§34.13). The final acceptance, `make e2e-cleanroom` from fresh platform state, passed
(§34.12).

### 34.12 Verification

Audited at HEAD 26b9df0 plus the A9 working tree, on the local stack
(`make local-up`; api and worker images rebuilt from the tree, migration
`f6c2a8d4e710` applied, downgrade and re-upgrade round-tripped). nuScenes mini
`scene-0061` (a 355,793,127-byte, 8,897-message recording) through the real stack,
API only:

```text
make test                       worker 487 passed / 30 skipped, api 104, inference-server 37,
                                analytics 151 / 2 skipped, core 560, integrations 47, streaming 39
                                (each suite in its own process; run in one process the worker and
                                inference-server conftests collide, which `make test` now avoids)
make test-integration           sceneops-db + sceneops-storage 69 passed; registrars and recording
                                Scene / Episode verticals 30 passed. Includes the check that the
                                migrated schema has no column the models dropped.
make test-infrastructure        20 passed, 3 skipped (the Airflow module): surface, dedup / force,
                                registration idempotency, convergence and conflict-then-replacement
                                for both domains, blocked resumption, failure recovery, 3 concurrent
                                runs over one scope, Celery, MinIO selective reads
make test-infrastructure-airflow  4 passed: recording_scene_building, recording_episode_building,
                                episode_learning_data_building and scene_ml_evaluation through the
                                four DAGs (the API restarted with the airflow backend, then restored)
make check-commands             consistent: 98 targets, 5 E2E journeys
ruff (apps, packages)           clean
make e2e-batch-canonical        exit 0. 3 Scenes + 1 Episode from one RobotRun, 606 payload artifacts
                                shared by both builds (the Episode build created none), every unit
                                validated / profiled / ready, bootstrap re-run changed no record.
make e2e-streaming-equivalence  exit 0. 20 channels / 8,897 messages equivalent, 3 Scenes + 1 Episode
                                equivalent, negative control rejected.
make e2e-scene-ml               exit 0 (mock backend). 4,699 labels over 39 samples; 3 views, 219
                                samples, 5 dropped anchors; ScenarioSet with 2 selected Scenes; 9
                                samples, 884 predictions, 880 tp / 4 fp / 165 fn; a retried predict
                                and evaluate converge; a real 34,688-point lidar payload decodes to
                                the source points. The mock backend perturbs the labels, so the
                                metrics prove wiring and pinning, not model quality.
make e2e-episode-learning       exit 0. 1 Episode aligned to 98 steps, export of 98 steps / 784
                                signals, same revisions and export id on retry, validate / profile
                                pass, LeRobot round trip: 1 episode, 98 frames, observation dim 4,
                                action dim 3, 5 fps, every frame equal to the export's dense window;
                                a rerun against the populated target is refused.
make e2e-cleanroom              exit 0 (FORCE=1, run as the final acceptance). Images rebuilt from the
                                tree; `local-reset` removed the PostgreSQL / Redis / MinIO volumes
                                (Kafka, Airflow and data/raw untouched); the RobotRun was acquired,
                                published and registered from scratch; canonical-bootstrap built
                                3 Scenes + 1 Episode (L1/L2 only); canonical-verify equalled the
                                bootstrap summary; e2e-scene-ml and e2e-episode-learning ran on that
                                baseline (BASELINE_ID=canonical, RobotRun reused); final API
                                verification: all four pipeline types succeeded (1 scene_ml, 2
                                episode-learning, 3 + 3 building runs), no job failed, every derived
                                artifact kind registered.
```

`make acceptance-grounding-dino`, `make acquisition-test`, `make lerobot-test` and
`make ros2-test` were not run (no inference server; the isolated environments were
not synced; the code under them is unchanged except the `default_task` field,
covered by the analytics unit tests).

Defects the real verticals exposed and A9 fixed: a LeRobot export of canonical
Episodes failed because LeRobot requires a task and canonical Episodes have none
(`ExternalExportConfig.default_task`); and, on this recording, no numeric channel
is resolved at every step under the default association (the recorded control
stream is sparse, and "previous" cannot resolve before its first sample), so the
journey states a wide nearest-association policy explicitly (§34.13).


### 34.13 Remaining operational debt (not decided here)

```text
Airflow         the per-task DAGs are a proof-of-concept orchestrator: serial tasks, an API
                backend chosen at process start, DockerOperator workers. Acceptance is opt-in.
Streaming       no automatic capture -> publish -> register hand-off; no process-restart or
                Kafka-rebalance recovery for continuous multi-run capture.
Scale           measured on nuScenes mini scene-0061 (one RobotRun, 3 Scenes, 1 Episode); larger
                workloads are not measured. One baseline stores ~0.5 GB in MinIO and nothing
                reclaims it except `make local-reset`.
Derived layer   nearest / previous association only (no pose interpolation); no frame transforms in
                evaluation; Episodes have no label sets; learning export is numeric only; dense
                projection has one missing-value policy (error), so a sparse stream needs an
                explicit wide association policy to be exportable.
Backends        the GroundingDINO backend is accepted separately (`acceptance-grounding-dino`) and
                was not run in the A9 verification.
Dataset tooling `tools/dataset-acquisition` reads nuScenes only; another dataset is another tool.
```

### 34.14 Sections affected

Supersedes the pipeline names of §17.5, §22.5 and §33.8 (`SCENARIO_CURATION`,
`DETECTION_EVALUATION`, `ALIGNED_EPISODE_BUILDING` → §34.2) and implements the
step-11 row of §29.19, §22.6 (E2E / baseline) and §33.11 (every deferred item:
`SceneManifest.annotations` / `annotation_count`, DatasetVersion `required_channels`
and metadata, telemetry / `raw_source` naming, ONNX dependencies, the unavailable
scripts). §16's DatasetVersion table is now fully implemented. §13.7's allowance for
embedded source annotations is withdrawn by I-60. Active documentation
(`docs/architecture/*`, `docs/development/*`, `README.md`) describes the system as
of A9.

---

## 35. Amendment A10: ingestion-mode terminology and the golden reference contract

### 35.0 Scope and result

A10 decides no ingestion or canonicalization question. RobotRun, registration,
identity, Scene / Episode canonicalization and the derived layer are unchanged. It
records two things about how the reference baselines are named and promoted.

### 35.1 Terminology

```text
Source preparation      nuScenes -> locked MCAP                         (reference-data-bootstrap)
Recording Import        existing MCAP -> RobotRun                       (the `ref-<scope>` baseline)
Streaming Acquisition   ROS 2 -> Kafka -> capture -> RobotRun           (the `stream-ref-<scope>` baseline)
RobotRun boundary       the immutable recording + manifest both modes end at
Canonical platform      RobotRun -> Scenes -> Episodes -> derived layers
```

Source preparation is not an ingestion mode. "Batch" in earlier sections of this
ADR (the "batch" baseline, the `e2e-batch-canonical` journey, "batch MCAP") names
Recording Import; the earlier text is not rewritten, and command names are unchanged.

### 35.2 Golden reference contract

The full-corpus baselines of both modes are promoted to an explicit, versioned
contract (`config/reference/<corpus>/reference_contract.json`): every fixture of
the corpus scope, ingested once through each mode under a deterministic identity
(20 RobotRuns, 20 whole-recording Scenes and 20 Episodes for `nuscenes-mini-v1`).
The restriction to one Scene and one Episode per RobotRun belongs to the contract and
the baseline build configuration only; the platform keeps supporting any recording
count, any Scene segmentation policy and several Scenes per RobotRun. The contract
is verified read-only and converged by composing the existing baseline bootstraps;
it never replaces a registered RobotRun. See
[`docs/development/reference-contract.md`](../development/reference-contract.md).

### 35.3 The reference environment and disposable test state

**Decision.** The local reference environment holds exactly the golden reference
contract. Generated runtime state is disposable and is reconstructed, never
selectively deleted:

```text
make local-reset                       drops PostgreSQL, Redis, MinIO, the Kafka log, the capture volume
make reference-contract-bootstrap      rebuilds the 20 RobotRuns from the locked corpus (data/reference)
make reference-contract-verify REQUIRE_CLEAN=1   20 RobotRuns / 20 Scenes / 20 Episodes, nothing else
```

`smoke-1` is a fixture selection (`scene-0061` of the contract's baselines), not a
baseline: no `ref-smoke-1` or `stream-ref-smoke-1` RobotRun or DatasetVersion exists.

**Why.** Canonical identity is immutable and the platform removes none of it (no
delete API for RobotRuns, Scenes, Episodes, Datasets, Jobs or ArtifactRecords; nothing
under `robot_runs/` is deleted, ADR-008 §6). A test-only deletion lifecycle, whether
direct SQL or a purpose-built API, would bypass the semantics every other consumer
relies on (RESTRICT foreign keys, write-once pinned artifacts, lineage) and would be a
second, untested implementation of removal. The reference corpus is locked and
byte-identical, so reconstruction is reproducible by construction and costs only time.

**Consequences.**

- Workflows are classed by what they leave behind (`READ_ONLY_REFERENCE` or
  `MUTATING_ACQUISITION_TEST`, [test-matrix.md](../development/test-matrix.md)). The first
  consume the contract's RobotRuns, register none and write only into
  `sceneops-test-*` DatasetVersions of their own; the second register RobotRuns of
  their own and refuse to run without `DISPOSABLE_RUNTIME=1`.
- A runtime that ran a mutating workflow is not the reference environment until it is
  reset and rebuilt; `REQUIRE_CLEAN=1` is the check, and it applies to the dedicated
  reference environment only (a general development platform may hold any RobotRun).
- Isolating the mutating workflows on a runtime of their own (separate Compose project
  and network) is a separate piece of work; until then the guard and the reset are the
  mechanism.

## 36. Amendment A11: acceptance surface after test consolidation

### 36.0 Scope

A11 changes the acceptance surface, not the architecture: it decides no ingestion,
canonicalization or derived-layer question. The "exactly five E2E journeys" of §34.0
and §34.5 and the `e2e-batch-canonical` rows of §34 and §35 are point-in-time
statements and are not rewritten; this section supersedes them.

### 36.1 The journeys

```text
e2e-streaming-equivalence    the locked reference MCAP in the contract's Recording Import
                             RobotRun and streamed through ROS 2 -> Kafka -> capture: equivalent
e2e-scene-ml                 Scenes -> labels -> sample views -> ScenarioSet -> prediction -> evaluation
e2e-episode-learning         Episodes -> AlignedEpisodes -> learning export -> verification + LeRobot round trip
e2e-cleanroom                fresh state -> canonical-bootstrap -> both L3 journeys -> verification
```

The acceptance surface is these four plus the opt-in model-backend acceptance
(`acceptance-grounding-dino`). The number of journeys is not itself a contract;
`make check-commands` pins the set.

### 36.2 `e2e-batch-canonical` is removed

**Decision.** The former batch-canonical journey registered a RobotRun of its own from
one fixture and asserted the Scenes and Episodes built from it. Every claim it made is
now carried by a surviving check, and it is deleted rather than kept beside them:

| Claim | Where it is verified now |
| --- | --- |
| Recording Import produces the registered RobotRun, Scene and Episode of a real recording: the run pins exactly the locked recording, the locked message and channel facts, one whole-recording Scene and one Episode, validated, profiled and ready, DatasetVersion summaries agree | the golden reference contract (`make reference-contract-verify`, `canonical-verify`; `scripts/reference/tests`) |
| Whole-recording Scene / Episode shape: windows on the declared source clocks, `[running marker, completed marker + 1)`, observed channels verbatim, streams kept asynchronous and unresampled, values selected by configuration, no outcome or annotation field | unit over a synthetic ROS 2 CDR recording (`apps/worker/tests/{scenes,episodes}/test_recording_*_builder.py`, `test_scene_manifest.py`, `packages/sceneops-core/tests/test_episodes.py`); the contract's shape check on the real corpus |
| Retry and convergence: re-running the bootstrap, a repeated build and a repeated registration change no record | `canonical-bootstrap` create-or-verify and the contract's snapshot diff; `tests/infrastructure/test_pipeline_execution.py`; `again.created_payload_count == 0` in the recording verticals |
| Scene and Episode independence | `test_scene_and_episode_builders_are_independent_siblings`; the recording Episode vertical builds both domains over one RobotRun on real PostgreSQL / MinIO |
| Payload sharing and canonical semantics: one observation payload set on the RobotRun, reused by the Episode build, ids independent of build configuration, manifests checksum-pinned | `test_recording_episode_vertical_integration.py`, `test_recording_scene_vertical_integration.py`, `test_scene_registration_integration.py` |

The journey's `MUTATING_ACQUISITION_TEST` class shrinks to `e2e-streaming-equivalence`
and any bootstrap with a non-contract `BASELINE_ID`.

**Residual gap.** No check compares the per-topic occurrence counts of the real
recording's Episode manifest (actions, states, observations) with the lock's
`topic_counts`; the real-recording check is the lock's counts on the registered
RobotRun plus Scene / Episode equivalence in `e2e-streaming-equivalence`, and the
unresampled-streams property is proven on the synthetic recording. It is recorded, not
recreated as another journey.

### 36.3 Other changes to the surface

- `smoke-api` (an API liveness probe), `check-minio`, the GroundingDINO endpoint check
  and `tests/infrastructure/test_pipeline_surface.py` are removed. The four-pipeline
  surface is proven by the unit tests over the pipeline definitions
  (`apps/worker/tests/pipelines/test_pipeline_definitions.py`).
- `make test-integration` discovers modules named `*_integration.py` instead of
  listing them, plus the `sceneops-db` and `sceneops-storage` suites by directory.
  The MinIO selective-read test lives with the package it exercises
  (`sceneops-analytics`), owns only the storage contract and needs no baseline.
- A real-infrastructure command (`test-integration`, `test-infrastructure`,
  `test-infrastructure-airflow`, `test-recovery`) fails if any test is skipped:
  a stopped stack must not read as green. Opt-in modules with their own target are
  excluded from the others' scope, not skipped.
- The Airflow Scene ML test builds Scenes into a DatasetVersion of its own and imports
  its own LabelSet from the fixture's locked reference labels; it never writes into the
  reference DatasetVersion.

### 36.4 Not decided here

Making `e2e-streaming-equivalence` read-only over the contract's two RobotRuns and
giving each L3 journey one fixed, converging `sceneops-test-*` identity are accepted
directions, implemented separately; until then the behavior of §35.3 stands for them.

### 36.5 Integration and recovery tests run in a disposable database and bucket

**Decision.** `make test-integration` and `make test-recovery` run in a PostgreSQL
database (`sceneops_test`, migrated with the repository's migrations) and a MinIO
bucket (`sceneops-test`) that exist only for the run: created first, dropped after it,
recreated by the next run if an interrupted one left them behind. They never connect
to the reference database or bucket, and no fixture deletes rows or objects to restore
shared state.

**Why.** Each integration run registered RobotRuns of its own into the reference
database and left them there, and the platform has no removal path to undo that
(§35.3). Dropping an environment that was created for the run needs no deletion
semantics at all; per-row cleanup was a second, untested removal implementation that
also had to be kept in step with every foreign key. The disposable names are the only
ones the runner will act on, so the destructive step cannot be pointed at the reference
environment.

**Consequences.** §35.3 and §36.4 stay in force for every workflow that runs on the
reference environment. The recovery suites keep their own Redis container and Celery
workers; only their PostgreSQL and MinIO moved. See
[test-matrix.md](../development/test-matrix.md).

### 36.6 `e2e-streaming-equivalence` is read-only over the contract's two RobotRuns

**Decision.** `e2e-streaming-equivalence` implements the first accepted direction of
§36.4. For one fixture it reads the golden reference contract's Recording Import
RobotRun and Streaming Acquisition RobotRun (both identities resolved from the
contract) and compares what is registered: each RobotRun's recording is read from the
ArtifactStore and checked against its registered checksum and manifest, and the two are
compared on acquisition (§29.12), on canonical Scene / Episode content (I-35) and by
negative controls. It replays, captures, publishes, registers and builds nothing,
needs no Kafka, ROS 2, replay container, capture volume or reference cache, and
creates no durable state: it fingerprints every RobotRun, Dataset, Scene and Episode
before and after and fails on any difference. Its class is `REFERENCE_READ_ONLY`; it
needs no `DISPOSABLE_RUNTIME`.

**Why.** The contract already holds the same acquisition twice, once per ingestion
mode. Streaming a third time into non-contract RobotRuns proved nothing the registered
pair does not, and left state that nothing removes (§35.3). The streaming path itself is
exercised, and its output registered, by the streaming baseline.

**Where the rest of the old journey's claims live.** The Kafka lifecycle of a streamed
run (one `RUN_START`, the run's telemetry records, one `RUN_END`, capture finalized by the
explicit `RUN_END`, the receipt's offsets spanning `message_count + 2` records) is
a property of the live transport, so it is pinned against a real broker in
`ros2/capture/tests/test_lifecycle_integration.py` with the real bridge node
(`make ros2-test`). The streaming baseline checks per fixture that replay, bridge,
capture and receipt carry the locked per-channel counts.

**Consequences.** `MUTATING_ACQUISITION_TEST` now covers only a bootstrap with a
non-contract `BASELINE_ID`. The measured scope is one fixture per run (default
`scene-0061`) against the registered recordings of the local reference environment;
the equivalence of the other nine fixtures is carried by `make streaming-compare`
(counts and canonical projections, no payloads) and by running the journey with
`SCENE=<fixture>`.

### 36.7 Derived workflows converge on fixed identities; one test-state vocabulary

**Decision.** The workflows that consume the golden contract and write state of their
own (the Scene ML and Episode learning journeys, the model-backend acceptance, the
pipeline and Airflow infrastructure tests) own a fixed Dataset each and converge on it:

```text
sceneops-test-scene-ml                   e2e-scene-ml
sceneops-test-scene-ml-grounding-dino    acceptance-grounding-dino
sceneops-test-episode-learning           e2e-episode-learning
sceneops-test-infra-pipelines            test-infrastructure   (one DatasetVersion per test)
sceneops-test-infra-airflow              test-infrastructure-airflow (one DatasetVersion per test)
```

No timestamp, counter or UUID is part of a top-level Dataset identity. Derived records
carry ids derived from it (`scset-<dataset>`, `infer-<dataset>`, `eval-<dataset>`), so a
repeated run reuses the Dataset, DatasetVersion, Scenes, Episodes, LabelSet, sample views,
ScenarioSet, InferenceRun, EvaluationRun, aligned Episodes and learning export instead of
adding new ones. Where a test needs a changed state, it uses the platform's explicit
replacement; nothing is deleted. `e2e-cleanroom` is unchanged: it resets the runtime
before it creates its one timestamped DatasetVersion.

The test-state classes are renamed to one vocabulary: `REFERENCE_CONTRACT`,
`REFERENCE_READ_ONLY`, `REFERENCE_DERIVED`, `MUTATING_ACQUISITION`,
`CLEANROOM_ACCEPTANCE` and `DISPOSABLE_ENVIRONMENT`. `READ_ONLY_REFERENCE` of §35.3 is
`REFERENCE_DERIVED` (the workflows that write derived state) or `REFERENCE_READ_ONLY`
(those that write nothing); `MUTATING_ACQUISITION_TEST` is `MUTATING_ACQUISITION`.
`REQUIRE_CLEAN` of §35.3 is replaced by `REQUIRE_PRISTINE`:
`reference-contract-verify` judges the contract alone and reports non-contract RobotRuns,
derived test datasets and foreign datasets apart, and `REQUIRE_PRISTINE=1` additionally
fails on any of them. The contract stays valid while derived test datasets exist.

**Why.** Platform state is never deleted piecemeal (§35.3), so a test identity that
changes per run accumulates until the next reset. A fixed identity bounds the top-level
state, keeps it recognisable as test-owned and lets the contract verifier tell it from
the contract.

**Consequences.**

- Execution history is append-only and is not derived state: forced Jobs and PipelineRuns
  add rows, and a re-executed validation, profile, scenario-mining or readiness stage
  writes a report keyed by the id of the Job that ran it (no per-stage id override
  exists). The journeys avoid re-executing the Scene ML pipeline and the first Episode
  learning pipeline (`force: false`); the infrastructure suites exist to prove
  re-execution and so append such reports on every run.
- `evaluate_detection` registers its ArtifactRecords under fresh ids on every execution,
  so executing it again under an existing evaluation run id duplicates the records of the
  same objects. The Scene ML journey therefore evaluates its recheck run once and reads it
  afterwards. Making those registrations idempotent is not decided here.
- A runtime that ran the previous, timestamped journeys holds `sceneops-test-*` Datasets
  that the fixed identities do not reuse; they are removed by `make local-reset`.
