# ADR-007: Canonical Ingestion Architecture

## Status

**Accepted — implementation pending.**

This ADR freezes the *target* ingestion architecture for SceneOps. At the time
of acceptance almost none of the target contract is implemented. Throughout
this document three labels are used wherever confusion is possible:

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

Relationship to earlier ADRs:

- [ADR-001](./001-postgresql-operational-metadata.md),
  [ADR-002](./002-object-storage-for-assets.md),
  [ADR-006](./006-parquet-for-analytical-data.md): unchanged. This ADR applies
  their PostgreSQL / Object Storage / Parquet responsibility split to ingestion.
- [ADR-005](./005-ros2-vs-kafka-boundary.md): unchanged. This ADR defines what
  happens *after* the ROS2 → Kafka → capture boundary that ADR-005 owns.

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
| `ExternalUnitSource` | Value | Provenance block: unit came from an external dataset (§14). |
| `RecordingSegmentSource` | Value | Provenance block: unit came from a registered recording (§14). |
| `ProducerInfo` | Value | Provenance block: which producer semantics and configuration built the unit (§14, §15). |
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

`ExternalDatasetRef.uri` stays provenance only (§14.3).

For recording sources, the recording is already a SceneOps-owned,
checksum-verified artifact (§7, §12.4). Canonical payload references for
recording-derived units must also resolve only to SceneOps-owned verified
artifacts. Whether they point to extracted per-observation artifacts or to
addressable positions inside the registered recording artifact is decided
by the Canonical Scene representation refactor.

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
     the sweeps is the debt, unless the Scene refactor explicitly defines a
     keyframes-only boundary.

Already compliant: per-frame `timestamp_us` comes from the source frame, not
from the sample timestamp, in both the nuScenes integration and the raw
builder. Episode building does not resample: `control_frequency_hz` is an
observed rate, and alignment is already the derived `ALIGN_EPISODE`.

---

## 14. Provenance model

### 14.1 Building blocks (`sceneops-core`, TARGET)

```text
ExternalUnitSource
  source_kind        = "external"
  external_ref       ExternalDatasetRef (format, format_version, external_name,
                                         external_revision, checksum?, uri)
  source_unit_key    str  (e.g. nuScenes scene token, LeRobot episode_index)

RecordingSegmentSource
  source_kind        = "recording"
  robot_run_id       str
  recording_artifact_id   str
  recording_checksum      "sha256:…"
  source_clock       str  (copied from RobotRunRecord / manifest)
  window_start, window_end   source-clock timestamps of the segment
  unit_key           builder-defined stable key within the recording
                     (e.g. segment start timestamp, mission boundary key)

ProducerInfo
  producer_id        stable producer identity (e.g. "sceneops.recording_scene_builder")
  semantics_version  int, bumped per §15.3
  build_config       normalized build configuration (canonical JSON value)
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
- `ExternalDatasetRef.uri` is **provenance only**. It is never part of
  canonical identity, and downstream workflows never use it to resolve
  payload bytes (§20).

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
    "producer_id":        …,
    "semantics_version":  …,
    "build_config":       normalized build configuration,
    "source_identity":    …
})))

source_identity
  recording:  { "robot_run_id", "recording_checksum" }
  external:   { "format", "format_version", "external_revision", "checksum"? }
```

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
| Source format on dataset identity | `Dataset.type = nuscenes/waymo/kitti` | Per-unit `ExternalUnitSource.external_ref.format` |
| Source paths on DatasetVersion | `raw_source_root_uri`, `required_channels` | Builder input / pipeline config |

Each concern moves to exactly one of: **adapter** (integration runtime),
**manifest contract**, **builder configuration**, or **derived workflow
configuration**. None moves to DatasetVersion or canonical identity.

### 20.3 External formats are integrations, not domain types

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
  uri / repository    location (provenance only; never identity, §14.3, §15.2)
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
configuration. A dynamic plugin registry is DEFERRED (§26).

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
```

Each invariant should be backed by at least one test at the layer that can
prove it (§24): unit tests for serialization and rules, real PostgreSQL for
I-10/I-11/I-12/I-14 concurrency, and real MinIO for I-3/I-4/I-9/I-24.
I-21–I-23 are backed by golden-manifest tests per producer and mapping /
contract tests per integration (source fixture → expected canonical
manifest). I-20 and I-25 are backed by import-boundary tests, which check
that SceneOps core does not depend on any integration SDK.

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
