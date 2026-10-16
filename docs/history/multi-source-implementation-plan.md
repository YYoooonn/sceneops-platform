# Multi-source implementation plan (M2–M5)

> Planning record written in M1. It is not a roadmap and not a current contract. It breaks
> the proposed [ADR-015](../adr/015-multi-source-platform.md) into implementation
> milestones with acceptance criteria, as of the commit below. When it disagrees with the
> repository at implementation time, the repository wins and the milestone is re-audited
> before work starts. Evidence:
> [audit](./multi-source-architecture-audit.md), [probe](./multi-source-dataset-probe.md).

```text
date     2026-10-16
branch   refactor/multi-source-platform
HEAD     bfd077e
policy   architectural correctness over backward compatibility: ids, APIs, schemas,
         PipelineTypes, job params and E2E expectations may break; no compatibility shims
gate     ADR-015 accepted 2026-10-16. Decided: R1–R7 (ADR-015 Status), OQ-4.
         Open before M3: OQ-1, OQ-2, OQ-3, OQ-5, OQ-6, OQ-9, OQ-10. Open before M5: OQ-7
```

## 1. Order and dependencies

```text
M2 Canonical Domain Refactoring        identity, revisions, composition; RobotRun sources only
  │
  ├──▶ M3 Multi-Source Integration     snapshots, snapshot source binding, clock domains,
  │      │                             nuScenes / LeRobot builders
  └──▶ M4 Shared Data Processing       temporal engine, extractors, domain projections
         │   (M4.1, M4.2 for manifest scopes, M4.4 need only M2;
         │    clock-domain enforcement, M4.3 and M4.5–M4.7 need M3)
         ▼
       M5 E2E & Cleanroom Verification
```

Every milestone ends in this state:

- The repository is green on the milestone's own validation set.
- Active documentation describes what exists at that point.
- No branch is dormant: a schema variant lands together with its producer and tests.
  ADR-007 §29.9's objection to option B still applies.

## 2. Contract regeneration protocol (applies to M2, M3, M4, M5)

Each milestone builds its logic as new. Nothing is compared with, kept from or migrated
from the previous golden reference contract: its expectations are discarded and
regenerated from the new logic, then accepted through
[ADR-015](../adr/015-multi-source-platform.md) §12. The new output is never its own
reference.

```text
1 derive    expectations from preserved raw inputs (locked MCAPs, nuScenes tables and files,
            LeRobot Parquet) by the documented rules of the new logic, recomputing ids,
            checksums and compositions by the documented formulas; the verifier never calls
            the builder, registrar or composer it checks
2 negative  injected faults must fail the verifier: dropped observation, 1 ns shift,
            changed payload byte, wrong pin, relative timestamp given an absolute anchor
3 record    the verification result as a docs/history record; only then commit the new
            golden expectations
```

The independent verifier is a tool (`tools/reference/`), built in M2.10 against the M2
rules and extended by each later milestone with that milestone's rules.

## 3. M2: Canonical Domain Refactoring

**Goal.** Make Scene / Episode identity independent of DatasetVersion. Registered manifests
become revisions. A DatasetVersion is created complete and is never changed. Sources remain
RobotRun recordings only.

**Out of scope.** Source content and snapshots, `ContentUnitSource`, clock-domain declarations, the
`PayloadRef` union, alignment changes and label anchor changes.

### Work items

| # | Change | Affected modules |
| --- | --- | --- |
| M2.1 | `unit_id_schema` v2: `f(domain, source_kind, robot_run_id, unit_key)`, no DatasetVersion | `sceneops_core/provenance/identity.py`, `scenes/schemas/records.py` (`scene_id_for`), `episodes/schemas/records.py` |
| M2.2 | Recording unit keys carry the segmentation policy (ADR-015 §6.3); producer semantics versions bumped | `sceneops_scenes/recording_builder.py`, `sceneops_episodes/recording_builder.py`, `sceneops_core/{scenes,episodes}/recording_build.py` |
| M2.3 | Split unit and revision: `scenes` / `scene_revisions`, `episodes` / `episode_revisions`; a revision is `(unit_id, manifest_checksum)` and holds the projection columns; run records reference the revision | `sceneops_db/models/{scenes,episodes}.py`, repositories, converters, `postgres/*`, new Alembic migration, `packages/sceneops-db/tests/test_migration_schema.py` |
| M2.4 | Registration additive and idempotent per revision. Same unit + same checksum → no-op. Same unit + same fingerprint + different checksum → fail. Add the ADR-015 §6.4 durability gate (each referenced payload object present in the store with its recorded size, one listing per payload prefix; manifest read-back stays). Remove `replace`, the recording-scope fingerprint rule and summary writes | `apps/worker/sceneops_worker/{scenes,episodes}/registration.py`, `stores/{scenes,episodes,datasets}.py`, `jobs/schemas/params/{scene,episodes}.py` |
| M2.5 | Manifest keys unit-scoped: `{canonical_root}/{scenes\|episodes}/{unit_id}/manifest-<sha256>.json`; aligned-episode keys follow | `sceneops_scenes/artifacts.py`, `sceneops_episodes/artifacts.py`, [storage-layout.md](../architecture/storage-layout.md) |
| M2.6 | `COMPOSE_DATASET_VERSION` Job (ADR-015 §7): stage selector resolution, pin verification, summaries and `composition_checksum` inside the Job; commit version + members + summaries in one PostgreSQL transaction that writes rows only (every referenced artifact is already durable; composition writes no object); converge or fail on name reuse, which also covers a re-run after a worker died between the composition commit and `JobRunner`'s success commit. Add `dataset_version_members` (reusing the `SceneRevisionRef` shape for both domains) and `composition_checksum`. **Remove** the synchronous empty-version creation (`POST /datasets/{id}/versions`, `DatasetStore.create_version`), `lock_for_update` and the registrar summary writers | `sceneops_db/models/datasets.py`, `postgres/datasets.py`, `repositories/datasets.py`, `sceneops_core/datasets/schemas/`, `sceneops_core/jobs/schemas/{enums,registry}.py`, worker handler, `apps/worker/sceneops_worker/stores/datasets.py`, `apps/api/app/domains/datasets/` |
| M2.7 | Recording pipelines without DatasetVersion params; downstream jobs take revision pins, or a DatasetVersion they resolve to its pins once and record | `sceneops_core/pipelines/builtin.py`, `apps/worker/sceneops_worker/pipelines/input_resolver.py`, every handler under `jobs/{dataset,derived,scenarios,inference,evaluation}/` |
| M2.8 | Validation, profiling and readiness per revision; quality APIs address revisions | `sceneops_core/{scenes,episodes}/readiness.py`, `apps/worker/sceneops_worker/scenes/readiness.py`, `apps/api/app/domains/{scenes,episodes,datasets}/quality.py` |
| M2.9 | API: Scene / Episode list and get return unit + revisions; filter by source; DatasetVersion members endpoint; composition only through the Job | `apps/api/app/domains/{scenes,episodes,datasets}/` |
| M2.10 | Build the independent verifier (§2) from the M2 rules; discard and regenerate the golden reference contract, baselines and journey expectations from the new logic and accept them through §2 | `tools/reference/`, `tools/baselines/`, `tools/e2e/`, [reference-contract.md](../development/reference-contract.md) |

### Breaking changes

- Every Scene and Episode id, and every derived pin that names a unit id.
- The `scenes` and `episodes` tables.
- `REGISTER_*` params (`replace` removed) and `BUILD_RECORDING_*` params (DatasetVersion
  removed).
- Creating an empty DatasetVersion (endpoint and store method removed).
- Dataset and quality API shapes.
- Manifest object keys.
- Producer fingerprints of the recording builders (semantics bump).

Existing local databases and buckets are reset, not migrated. Nothing is compared with
the previous contract.

### Tests

- **Unit:**
  - identity golden values for v2;
  - unit-key derivation per segmentation policy;
  - the registrar's decision table: new unit, new revision, identical revision, and a
    conflicting checksum for the same fingerprint;
  - `composition_checksum` independent of member input order.
- **Integration on real PostgreSQL:**
  - two sessions registering the same revision converge to one row (unique constraint);
  - two Jobs composing the same `(dataset_id, version)` with the same composition both
    succeed with one row; with different compositions, one commits and the other fails with
    no partial members;
  - a composition that fails verification on its last pin, or crashes before commit, leaves
    no DatasetVersion row;
  - a composition committed whose Job is then reclaimed and re-run converges to a no-op
    (worker killed after the composition commit, before `JobRunner` records success);
  - the composing transaction issues no object-store call (asserted with a counting
    ArtifactStore, the existing `sceneops_analytics.testing.counting_artifact_store`
    pattern);
  - deleting a member revision is refused (RESTRICT);
  - registration with a payload object missing from the store, or present with another
    size, fails and writes no revision row, although its ArtifactRecord exists (the gate,
    not the record, decides);
  - a migration test checks every new FK's ON DELETE rule.
- **Real MinIO:** unit-scoped manifest keys are write-once, and a second build writes no
  object.
- **Verifier:** the negative controls of §2 step 2 each fail the verifier.
- **E2E:** `e2e-scene-ml`, `e2e-episode-learning` and `e2e-streaming-equivalence` on the new
  identities; `e2e-cleanroom`.

### Acceptance criteria

1. `make test` (including `check-boundaries` and `check-commands`), `make test-integration`
   and the three journeys pass.
2. `make e2e-cleanroom` rebuilds exactly 20 RobotRuns, 20 Scenes and 20 Episodes (one
   revision each), and a second bootstrap writes nothing.
3. The regenerated contract passes §2: its ids, checksums, compositions and semantics, for
   both the Recording Import and the Streaming Acquisition side, match the verifier's
   independent recomputation from raw inputs, and every negative control fails. The
   verification record is in `docs/history/`.
4. Rebuilding with an unchanged `build_config` adds no revision, artifact or object.
   A changed channel selection adds one revision per unit and leaves the earlier revision,
   and every DatasetVersion that pins it, unchanged.
5. One Scene revision is a member of two DatasetVersions with one manifest object.
6. No code path creates an empty DatasetVersion or writes one after commit. No repository
   method exists for either, and a test asserts it. No DatasetVersion status other than
   "exists" remains.
7. No `dataset_id` / `dataset_version` input remains in unit identity or canonical manifest
   keys (grep guard in a test).

### Risks

- **Contract churn is wide.** Every stored expectation is regenerated. Its only check is
  the verifier, so the verifier's rules must be written from the documentation before
  its first run against the new output, never adjusted to match it.
- **Latest-revision drift.** Downstream handlers that today scan a DatasetVersion must
  resolve pins once and record them. A handler that re-resolves mid-run reintroduces
  "latest".
- **Composition size.** Composing a large version is one PostgreSQL transaction. The member count at
  which it becomes a problem is not measured; the reference contract has 40 members.

## 4. M3: Multi-Source Integration

**Goal.** External datasets become sources of canonical units without a RobotRun (nuScenes →
Scene, LeRobot → Episode). Preservation (snapshot) and canonicalization (builder) stay
separate steps.

**Out of scope.** nuScenes Episodes and nuScenes CAN canonicalization (CAN is preserved
only), LeRobot Scenes, LeRobot v2.x, in-place remote snapshots (OQ-1) and alignment changes.

### Work items

| # | Change | Affected modules |
| --- | --- | --- |
| M3.1 | Core schemas: `ContentUnitSource`, `ContentSourceRevision`, fingerprint over the source-revision union, `ClockDomain` declarations (`clock_domains[]`), `PayloadRef` union with `SourceFileRef`, optional `native_ref`; SceneManifest v3, EpisodeManifest v2; content inventory, snapshot declaration / descriptor, content and snapshot record schemas; `content_id` / `snapshot_id` derivations (ADR-015 §3, §5). Recording build configs declare a clock origin (default `source`, OQ-9) | `sceneops_core/provenance/{sources,producer,identity,source_time}.py`, `artifacts/schemas/payload.py`, `scenes/schemas/manifests.py`, `episodes/schemas/manifests.py`, `{scenes,episodes}/recording_build.py`, new `sceneops_core/sources/` |
| M3.2 | Label anchor `(source_kind, source_id, channel, clock, timestamp_ns)`, plus the unit for `unit`-origin clocks; the sample-view label index compares source, not `robot_run_id` | `sceneops_core/labels/schemas.py`, `sample_views/build.py`, `apps/worker/sceneops_worker/jobs/derived/import_labels.py`, `integrations/dataset-acquisition/.../labels.py` |
| M3.3 | New package `sceneops-sources`: inventory hashing, declaration / descriptor, DB-free publication (files under `content_id`, write-once with reuse; inventory; descriptor last), verified content reader, `nuscenes/` and `lerobot/` readers (json + pyarrow, no SDK; each derives the format version from the bytes) | `packages/sceneops-sources/`, `tools/checks/import_boundaries.py`, [repository-structure.md](../architecture/repository-structure.md) |
| M3.4 | `python -m sceneops_publisher publish-snapshot`; Compose service with the raw-source mount; runtime-boundary check updated | `apps/publisher/`, `compose/*.yaml`, `tools/checks/runtime_source_boundary.sh` |
| M3.5 | `REGISTER_SOURCE_SNAPSHOT` Job and the `source_contents` / `source_snapshots` tables. It verifies preservation only (descriptor and inventory; every file on the content's first registration); it does not check builder support. It inserts content if absent and the snapshot if absent, in one PostgreSQL transaction, with the ADR-015 §5 conflict rules. `GET /source-snapshots`, `GET /source-contents/{id}` (with its declaring snapshots) | `sceneops_db`, migration, worker handler, `apps/api/app/domains/` (new `sources/` domain) |
| M3.6 | nuScenes Scene builder: every reading of the selected sensors (camera, lidar, radar), keyframe groups, calibration, per-reading ego pose, native refs, clock `nuscenes.timestamp` (origin `unix_epoch`); rejects a format version (read from the bytes) it does not support | `sceneops_scenes/content_builder.py` |
| M3.7 | LeRobot Episode builder: `observation.*` / `action` / `next.*` streams with values decoded exactly, task events from `tasks`, image streams as frame refs, clock `lerobot.frame_time` (origin `unit`), frame-time normalization (OQ-2) | `sceneops_episodes/content_builder.py` |
| M3.8 | `SNAPSHOT_SCENE_BUILDING` and `SNAPSHOT_EPISODE_BUILDING` pipelines (take `snapshot_id`, resolve its `content_id` once, build from content); registration verifies `SourceFileRef`s against the content inventory | `sceneops_core/pipelines/builtin.py`, `apps/worker/sceneops_worker/{scenes,episodes}/registration.py`, `recordings/payload_refs.py` |
| M3.9 | Payload consumers dispatch on reference type (detection image / lidar loading, Scene validation) | `apps/worker/sceneops_worker/inference/detection/samples.py`, `sceneops_scenes/validation/` |
| M3.10 | Native nuScenes `sample_annotation` → LabelSet against snapshot Scenes (if OQ-6 says M3) | `sceneops-sources` nuScenes reader, `IMPORT_LABELS` |

### Tests

- **Unit:**
  - inventory determinism (order, path normalization);
  - `content_id` depends on the file list only (not format, declaration or
    descriptor); `snapshot_id` depends on the declaration only (not bytes or
    annotations);
  - readers on synthetic fixtures (the acquisition tool's `synthetic_nuscenes.py` is the
    model) and a synthetic LeRobot v3 layout including both `names` forms;
  - builder golden manifests;
  - frame-time rounding at 10, 30 and 50 fps;
  - float32 decode round trip bit-exact;
  - clock-domain declarations validated, including that an Episode with a `unit` origin
    carries no absolute anchor field;
  - a cross-source schema test that runs the same SceneManifest / EpisodeManifest
    validators over recording-derived and snapshot manifests.
- **Integration (real MinIO + PostgreSQL):**
  - publication interrupted before the inventory leaves no registrable snapshot, and a
    re-run converges;
  - a file altered after publication fails registration;
  - the ADR-015 §3 case table, end to end. A second upstream revision with identical bytes
    registers a new snapshot, reuses the content (no file re-upload, no new object), and
    a rebuild through it adds no unit, revision or object. One declaration with different
    bytes, one declaration with a different descriptor, and the same bytes under another
    format each fail with no row written;
  - execution provenance: a build through snapshot B of content already built through
    snapshot A records B's `snapshot_id` in its Job result; the reused revision's
    manifest ArtifactRecord still names A's build Job; no manifest contains either id;
  - concurrent registration of one snapshot converges, and two snapshots of one content
    registered concurrently both succeed with one content row;
  - a snapshot whose bytes carry an unsupported format version registers, and its build
    fails loudly.
- **Boundary:** `sceneops-sources` imports no SDK, and the workspace lock contains no
  `lerobot` (existing `test_package_boundaries.py` pattern).

### Acceptance criteria

These are measured against the probe's numbers, by a verifier that reads the raw inputs
itself (§2 step 1).

1. **Preservation.** Every file under the snapshot's selection is stored byte-identically,
   once per content, including files no builder reads (CAN extracts, maps, licence). The
   inventory's sha256 values equal an independent hash of the raw tree. The declaration
   records upstream identity, upstream revision (pusht `7628202a…`; nuScenes `v1.0-mini`)
   and selection rules, and the format version the reader derives from the bytes matches
   the probe (`v3.0`, `v1.0-mini`).
2. **Deduplication with provenance.** Registering pusht a second time under a different
   declared revision with byte-identical selected files leaves one content and two
   snapshots, both listed for that content. Every unit id and manifest is unchanged.
3. **nuScenes `scene-0061` Scene.**
   - 2,963 observations on 12 channels (1,318 camera, 382 lidar, 1,263 radar).
   - 39 keyframe groups of 12 members each, and one ego pose per reading.
   - The window covers the 43.1 ms before the first sample and the 29.3 ms after the last.
   - Every `SourceFileRef` checksum equals the raw file's sha256.
   - The Scene contains no CAN stream.
4. **Cross-source consistency for the same fixture.** Camera observation timestamp values
   and payload checksums of the snapshot Scene equal those of the RobotRun Scene, and lidar
   timestamp values are equal. This is a verification of the conversion's numbers, not a
   merge of the two clock domains.
5. **LeRobot `pusht`.**
   - 206 Episodes and 25,650 frames in total, with each Episode's length equal to
     `meta/episodes` `length`.
   - Every `observation.state` / `action` value is bit-equal to the Parquet float32.
   - Timestamps are on `lerobot.frame_time` with origin `unit`, and the task string is
     preserved.
6. **Idempotency.** Re-running publication, registration and both pipelines adds no row,
   artifact or object.
7. **Mixed sources.** A DatasetVersion composed of RobotRun Scenes and nuScenes Scenes
   validates and reports both in its summary. No downstream module branches on
   `source_kind` outside payload resolution (grep guard).

### Risks

- **Video decoding.** AV1 decoding in the worker image is unverified. M3 stores frame
  references and does not decode; the first decoder is an M4 consumer.
- **Storage.** Snapshot storage doubles the raw footprint (9.4 GB for nuScenes mini). Host
  disk has wedged Docker here before, so size the reference bucket before M5.
- **Network.** CI and the clean room must read pinned local copies, never the HF hub.
- **Licences.** Each fixture's licence must allow the local copy the reference corpus keeps.

## 5. M4: Shared Data Processing

**Goal.** One source-independent temporal alignment capability over RobotRun, Scene and
Episode scopes, preserving clock-domain semantics. Domain outputs stay domain-specific.

### Work items

| # | Change | Affected modules |
| --- | --- | --- |
| M4.1 | `sceneops_core.temporal`: `TimestampedStream` (with clock domain), `AlignmentPolicy`, ns engine lifted from `episodes/alignment/policies.py`; ticks computed as `origin + round_half_even(k · 10⁹ / hz)`, replacing `start + n · dt_us` with a once-rounded µs period (`quantize_period_us`), which drifts n/3 µs per tick at 30 Hz | `sceneops_core/episodes/alignment/` → `sceneops_core/temporal/` (no forwarding module) |
| M4.2 | Stream extractors: Episode revision, Scene revision, RobotRun recording (through the recording reader), each carrying the clock domain and origin instance | `sceneops_core/temporal/` (manifest scopes), `sceneops_recording` (recording scope) |
| M4.3 | `AlignedEpisode` on the engine: ns timestamps with their clock domain, no absolute-anchor field for `unit` origins; alignment semantics version bump; task from canonical task events when present | `sceneops_core/episodes/alignment/{engine,schemas,semantics}.py`, `apps/worker/sceneops_worker/jobs/dataset/align_episode.py` |
| M4.4 | `SceneSampleView` association on the engine kernel, with the same policy vocabulary and tie rules | `sceneops_core/sample_views/build.py` |
| M4.5 | Learning export keeps image references (persist the reference artifact or snapshot ref, not only `reference_uri`) and clock-domain columns beside every timestamp | `sceneops_analytics/learning_tables*.py`, `learning_dataset/reconstruct.py` |
| M4.6 | `EpisodeSet`: pinned Episode revisions + recorded criteria; curation produces one | `sceneops_core/episodes/curation/`, `CURATE_EPISODES` |
| M4.7 | Learning export and LeRobot export from LeRobot-sourced Episodes. The external export maps a `unit`-origin timestamp to LeRobot's episode-relative time and claims no absolute anchor; it classifies `TIMESTAMPS` by what is actually lost | `EXPORT_LEARNING_DATA`, `sceneops_analytics/external_adapters/lerobot/` |

### Tests

- **Unit:**
  - kernel properties: determinism, earlier-wins tie, tolerance and max-gap boundaries,
    no interpolation of non-numeric values;
  - clock-domain enforcement: streams on different clocks are rejected; two units on the
    same relative clock are rejected; an epoch and a `source` origin are never mixed;
  - ticks at 10, 30 and 50 Hz equal the normalization grid exactly;
  - golden alignments at ns.
- **RobotRun scope:** extraction from the `scene-0061` reference recording yields the same
  per-topic sample counts as the recording (`prepare-log.jsonl` topic counts).

### Acceptance criteria

1. `SceneSampleView` manifests and `AlignedEpisode` outputs of the reference fixtures match
   the verifier's independent association and alignment at exact ns (§2).
2. Scene and Episode association use the same engine kernel and policy vocabulary.
3. `pusht` Episodes (10 fps) and a synthetic 30 fps and 50 fps Episode, each aligned at its
   native rate on `lerobot.frame_time`, resolve every channel at every step with `exact` and
   zero delta.
4. Every aligned output and export row of a LeRobot-sourced Episode keeps origin `unit`.
   No absolute time appears anywhere downstream (asserted by a negative control that injects
   one).
5. LeRobot import → align → export → LeRobot reader reproduces every frame's
   `observation.state` and `action` of the 206 `pusht` episodes, their task strings, and
   frame times within float32 precision of the source `timestamp`.
6. `learning_signals` rows for image channels resolve to the referenced payload.

### Risks

- **Checksum churn.** Moving the engine to ns changes every aligned checksum. Derived test
  state is regenerated only through §2.
- **Recording clock origins.** Recording build configs default to origin `source` (OQ-9).
  A config that asserts `unix_epoch` wrongly would make RobotRun clocks look comparable
  across runs.

## 6. M5: E2E & Cleanroom Verification

**Goal.** Prove both ingress families end to end from preserved inputs on an empty runtime,
then make the active documentation describe the new system.

### Work items

- **Reference corpus.** Add the nuScenes mini snapshot and the pinned `pusht` revision as
  locked inputs, extending `make reference-data-bootstrap` / `reference-data-verify` with
  their content inventories, snapshot declarations, descriptors and checksums.
- **Golden reference contract.** The 20 RobotRuns plus 2 snapshots and their units, by
  fixed identity, accepted through §2.
- **Journeys.** This is an E2E-surface change in `tools/checks/command_surface.py` and
  [test-matrix.md](../development/test-matrix.md).
  - `e2e-scene-ml` runs on native nuScenes Scenes (labels from native annotations).
  - `e2e-episode-learning` runs on LeRobot-sourced Episodes with the LeRobot round trip.
  - `e2e-streaming-equivalence` keeps covering RobotRun Scenes / Episodes.
  - One journey composes a mixed-source DatasetVersion.
  - OQ-7 decides whether the recording-sourced variants of the two derived journeys stay.
- **Failure injection** at the new boundaries: snapshot publication crash, registration
  retry, concurrent composition, and a composition crash before commit.
- **Documentation.**
  - Rewrite the active documents: overview, data-model, scene-domain, episode-domain,
    derived-layer, robot-learning-data, dataset-interoperability, storage-layout,
    repository-structure, quality-and-runs, limitations, test-matrix and
    reference-contract.
  - Change ADR-015 from "accepted, not yet implemented" to implemented, and ADR-007's
    "Superseded in part" note to past tense (both were set at M1 acceptance).
  - Add a clean-room acceptance record in `docs/history/`.

### Acceptance criteria

1. `make e2e-cleanroom` on a reset runtime reconstructs the extended contract from preserved
   inputs alone, and the independent verifier passes on it (§2 steps 1–2). A second
   bootstrap imports, publishes, builds and writes nothing.
2. Run twice, each journey adds no Dataset, unit, revision, artifact or object the second
   time.
3. The mixed-source DatasetVersion is reproducible: composing it again from its recorded
   pins yields the same `composition_checksum`, members and summaries.
4. `make check-commands` and `make check-boundaries` pass with the new surface, and no
   active document describes RobotRun as the only ingress.

## 7. Explicitly excluded from M2–M5

- Live robot control.
- Non-MCAP recordings.
- Cross-clock mapping.
- RLDS or other external formats.
- nuScenes Episodes, nuScenes CAN canonicalization and LeRobot Scenes.
- LeRobot v2.x.
- In-place remote snapshots.
- Per-file ArtifactRecords for snapshots.
- Persistent draft DatasetVersions.
- A web UI.
- Spark, Flink, new brokers or new databases.
