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

### 29.15 Annotations and source-defined groupings (open, Q1)

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
Q1  Ground truth and keyframe groupings after the nuScenes Scene integration is removed
    (§29.15). Blocks restoring detection evaluation (step 10/11); does not block 6–9.
    Recommendation: a label ingress anchored to L1 message identity (§29.15).
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
