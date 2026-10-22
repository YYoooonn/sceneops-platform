# Multi-source implementation plan (M2–M5)

> Planning record written in M1. It is not a roadmap and not a current contract. It breaks
> the proposed [ADR-015](../adr/015-multi-source-platform.md) into implementation
> milestones with acceptance criteria, as of the commit below. When it disagrees with the
> repository at implementation time, the repository wins and the milestone is re-audited
> before work starts. Evidence:
> [audit](./multi-source-architecture-audit.md), [probe](./multi-source-dataset-probe.md),
> [M3.0 native integration architecture](./multi-source-native-integration-architecture.md).

```text
date     2026-10-16 (M3 section re-planned 2026-10-22 at 4b11e5e, M3.0)
branch   refactor/multi-source-platform
HEAD     bfd077e
policy   architectural correctness over backward compatibility: ids, APIs, schemas,
         PipelineTypes, job params and E2E expectations may break; no compatibility shims
gate     ADR-015 accepted 2026-10-16. Decided: R1–R7 (ADR-015 Status), OQ-4; OQ-1, OQ-2,
         OQ-3, OQ-5, OQ-6, OQ-9, OQ-10 and M3.0 details D1–D14 decided 2026-10-22 (ADR-015
         §16, M3.0 record §9). Open: OQ-7 (before M5), OQ-8
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

### Decisions and boundaries

M3 implements the decisions recorded in ADR-015 §16 and the
[M3.0 record](./multi-source-native-integration-architecture.md) §9:
- OQ-1: always copy.
- OQ-2: frame-index time, with `fps` stored once on the Episode `frame_timeline` and not on
  the clock domain.
- OQ-3: inline values.
- OQ-5: `scene.token`.
- OQ-6: native labels in S6, anchored on the sample (`group_ref = sample.token`) and
  independent of sample views.
- OQ-9: recording origin `source`.
- OQ-10: no correction.
- Details D1–D14, where D3 means builder-assigned media types, D8 is `component_names` and
  D9 means `next.*` as `transition` streams.

The record's §5 fixes the process flow and the responsibilities of SourceContent /
SourceSnapshot, the Source Registry, native storage, native readers, pinned content,
builders and registration. It also fixes the schemas and interfaces listed below.

The original M3.1–M3.10 work items are regrouped into vertical slices. Each slice lands a
schema variant together with its producer and tests (no dormant branch), extends the
independent verifier (§2) with its rules before its first platform run, and updates the
active documents whose contract it changes. Integration runs on disposable runtimes.

### Prerequisites (before S1)

- Done (2026-10-22): the M3.0 decisions are accepted and ADR-015 §16 records them.
- The user frees Docker VM space. On 2026-10-22, 4.3 GiB was free. S1 needs ~3.6 GiB with
  preflight margins plus a ~2 GB transient image rebuild, and S4 needs ~4.8 GiB, so
  **neither fits today**. Implementation never prunes Docker resources.
- First step of S1, before any publication: pin `lerobot/pusht` at `7628202a…` locally
  (`data/raw/lerobot/pusht`, 7 files, 7,684,382 bytes) with a checksum lock in the
  reference-data tooling. The M3.0 copy lives only in a session scratchpad.

### Slices

| Slice | Content | Former items |
| --- | --- | --- |
| S1 Source preservation | `sceneops-sources` package (selection dialect D1, inventory hashing, DB-free publication, `ContentFiles` with stored / local implementations); `publish-snapshot`; `source_contents` / `source_snapshots`, ArtifactKinds and owner types; `REGISTER_SOURCE_SNAPSHOT {descriptor_uri}` (D11); `GET /source-snapshots[/{id}]`, `GET /source-contents/{id}`; capacity preflight for snapshots. No reader, builder or manifest change | M3.3 (infrastructure), M3.4, M3.5 |
| S2 Clock domains on the recording path | `ClockDomain`; `clock_domains[]` in SceneManifest v3 / EpisodeManifest v2; `ArtifactPayloadRef.kind`; recording builders declare origin `source` for every clock (OQ-9); semantics bumps. Lineage stays recording-only | M3.1 (shared part) |
| S3 LeRobot → Episode | EpisodeManifest v2 admits `ContentUnitSource`, `SourceFileRef` with frame selector, `native_ref`, `frame_timeline {clock, fps, frame_count}` (OQ-2a), `component_names` (D8) and the `transition` stream role (D9); `sceneops_sources.lerobot` reader; `sceneops_episodes.lerobot_builder` (OQ-2, OQ-3, D3 media type, D9, D13); `BUILD_SNAPSHOT_EPISODES` + `SNAPSHOT_EPISODE_BUILDING` with `unit_keys` (D2); `episodes` table per-kind source columns; Episode registration scope = one source; `SourceFileRef` gate (D3, D5); selector clause by source (D10); explicit refusal in alignment / learning export / LeRobot export (D14) | M3.1, M3.3, M3.7, M3.8 (Episode side) |
| S4 nuScenes → Scene | SceneManifest v3 admits `ContentUnitSource`, `SourceFileRef` and `native_ref` (observations: `sample_data.token`, keyframe groups: `sample.token`); `sceneops_sources.nuscenes` reader; `sceneops_scenes.nuscenes_builder` (OQ-5, D3 media types, D7); `BUILD_SNAPSHOT_SCENES` + `SNAPSHOT_SCENE_BUILDING`; `scenes` table per-kind source columns; Scene consumers resolve through the one payload locator (validation, profiling, sample views, detection samples) | M3.1, M3.3, M3.6, M3.8, M3.9 (Scene side) |
| S5 Mixed sources and M3 verification | mixed-source composition; grep guards; full idempotency; M3 verification record and the M3 slice golden expectations | M3 acceptance 6, 7 |
| S6 Native nuScenes labels | LabelSetManifest v2: anchor `(source_kind, source_id, channel, clock, timestamp_ns)` (+ `unit_key` for `unit` origins) with optional `group_ref` (OQ-6); one anchor resolver on the Scene manifest (observation, and keyframe group when `group_ref` is set), used by sample views and the verifier; sample-view label index by source; `nuscenes.read_annotations` reader and `sceneops_derived.nuscenes_labels` (new edge to `sceneops_sources`); `IMPORT_LABELS {snapshot_id}`; `render-labels` re-anchored for RobotRun Scenes and its locked label artifacts regenerated | M3.2, M3.10 |

S2 lands before S3 / S4 so that clock domains are proven on the recording path, whose
verifier rules already exist, before content sources depend on them. Recording manifest
bytes change again in S3 / S4, when EpisodeManifest v2 / SceneManifest v3 gain their
optional fields, because absent fields serialize as `null` or `[]`. That costs nothing
stored: golden expectations are committed only after S5 verification, and the schema
versions are final when S4 lands. S3 precedes S4 because pusht fits on less disk and
exercises the `unit` origin, the semantics most likely to be wrong. S6 is separable: Scene /
Episode acceptance does not depend on it.

### Tests carried into the slices

- **Unit:** selection dialect and inventory determinism (S1); `content_id` from the file
  list only, `snapshot_id` from the declaration only (S1); clock-domain declarations (S2);
  frame-time rounding and its inverse at 10, 30 and 50 fps, float32 round trip bit-exact,
  `frame_timeline` validation (an off-grid or out-of-range occurrence, a window that
  disagrees with `frame_count`), `component_names` length, a `transition` stream with a
  missing or duplicated frame, readers on a synthetic LeRobot v3 layout with both `names`
  forms and both list types (S3); media-type evidence that disagrees fails (S3, S4); the
  anchor resolver on a synthetic Scene, including a `group_ref` that names another group,
  and a sample whose timestamp differs from its `LIDAR_TOP` keyframe failing import (S6);
  readers on a synthetic nuScenes layout modelled on the acquisition tool's
  `synthetic_nuscenes.py` (S4); builder golden manifests (S3, S4); one cross-source schema
  test running the same manifest validators over recording and content manifests (S4).
- **Integration (real MinIO + PostgreSQL):** the S1 failure, conflict and concurrency
  cases; execution provenance (a build through snapshot B of content built through A records
  B in its Job result, the reused revision's manifest ArtifactRecord still names A's Job); an
  unsupported format version registers and its build fails loudly (S3).
- **Boundary:** `sceneops-sources` imports no dataset SDK and the workspace lock contains no
  `lerobot`; the new package edges are declared in `tools/checks/import_boundaries.py` (S1).

### Acceptance criteria

Numbers are the M3.0 measurements (§2 of that record) and the M1 probe, checked by the
verifier from raw inputs.

**S1 Source preservation**

1. pusht, declared `(lerobot, lerobot/pusht, 7628202a…, [README.md, data/**, meta/**,
   videos/**])` with annotation `license=mit`, publishes 7 files / 7,684,382 bytes and
   registers one content and one snapshot. Inventory sha256 values equal an independent
   hash of the raw tree.
2. The `scene-0061` slice, declared `(nuscenes, nuscenes, v1.0-mini, …)` with the six
   patterns of the M3.0 record, publishes 2,989 files / 519,531,462 bytes, including keys
   that contain `+`, the 8 CAN extracts, 4 maps and `LICENSE`, and reads every key back on
   real MinIO.
3. A second pusht declaration with another `upstream_revision` and identical bytes leaves
   one content and two snapshots, both listed for the content, and writes no file object.
4. The ADR-015 §3 case table on real MinIO + PostgreSQL: one declaration with other bytes,
   one declaration with another descriptor, and the same bytes under another format each
   fail with no row written. A pattern that matches nothing, or uses `[`, fails publication.
5. A crash before the inventory leaves no registrable snapshot and a re-run converges; a
   crash after the descriptor is recovered by registration; a file altered in the store
   fails registration; concurrent registrations of one snapshot converge, and of two
   snapshots of one content leave one content row.
6. Re-running publication and registration writes no row, artifact or object.
7. `capacity-preflight` refuses a snapshot that does not fit the Docker VM.

**S2 Clock domains on the recording path**

1. Every Scene / Episode manifest declares each clock it uses exactly once; recording clocks
   have origin `source`. An undeclared or unused declaration is rejected.
2. `make test`, `make test-integration` and `SUITE=pipelines` pass; the recording Scene /
   Episode of `scene-0061` rebuilt on a disposable runtime passes the verifier with the
   clock-domain rule, and the origin-swap negative control fails it.
3. `e2e-scene-ml` and `e2e-episode-learning` (`scene-0061`) pass on disposable runtimes.

**S3 LeRobot → Episode (pusht)**

1. 206 Episodes with unit keys `"0"`…`"205"`, each with `meta/episodes` `length`
   occurrences per selected stream (25,650 per stream in total).
2. Episode 0: 161 frames, `frame_timeline {lerobot.frame_time, fps 10, frame_count 161}`,
   window `[0, 16000000001)`, clock domain `lerobot.frame_time` origin `unit` with no rate;
   every timestamp equals `round_half_even(frame_index · 10⁹ / 10)`, and no occurrence
   carries a `timestamp` or `frame_index` value.
3. Every `observation.state` / `action` value is bit-equal to the Parquet float32, and both
   fields declare `component_names ["motor_0", "motor_1"]`.
4. `observation.image` occurrences are `SourceFileRef`s with media type `video/mp4` to
   `videos/observation.image/chunk-000/file-000.mp4` (sha256 `f58d1185…`) whose frame
   selector follows the M3.0 rule (episode 1, frame 0 → 16,100,000,000 ns).
5. One `task` event at time 0 per Episode carrying "Push the T-shaped block onto the
   T-shaped target.". `next.reward`, `next.done` and `next.success` are `transition`
   streams with one occurrence per frame (25,650 each) and values in their source type:
   `next.done` is true on 412 rows (the last two frames of every Episode),
   `next.reward` > 0 on 20,725 rows, and `next.success` is never true. No event is derived
   from them.
6. No manifest contains a snapshot id, upstream, upstream revision or licence. Building
   through the second pusht snapshot reuses every revision, and its Job result names that
   snapshot.
7. Rejected loudly with no row: a non-grid `timestamp`, an unsupported `codebase_version`,
   an unknown `unit_keys` entry, a `SourceFileRef` whose checksum differs from the
   inventory, a referenced content object missing from the store.
8. Rebuilding adds no revision, artifact or object. Alignment and the two exports refuse a
   pusht Episode with an explicit error.

**S4 nuScenes → Scene (`scene-0061` slice)**

1. One Scene with unit key `cc8c0bf57f984915a77078b10eb33198`: 2,963 observations on 12
   channels (1,318 camera, 382 lidar, 1,263 radar), 39 keyframe groups of 12, and one ego
   pose per observation linked by `ego_pose_id`.
2. Window `[1532402927604844000, 1532402946826796001)` on `nuscenes.timestamp`, origin
   `unix_epoch`: 43.107 ms before the first sample and 29.279 ms after the last.
3. Every `SourceFileRef` sha256 equals the raw file's; media types follow the M3.0 §9 D3
   table (`image/jpeg`, `application/x.nuscenes.lidar-pcd-bin`,
   `application/x.nuscenes.radar-pcd`); every observation's `native_ref` is its
   `sample_data.token` and every keyframe group's its `sample.token`; no CAN stream; no
   payload object is written.
4. Cross-source: camera timestamps and payload checksums equal those of the RobotRun
   `scene-0061` Scene, and lidar timestamps are equal (a comparison of numbers, not of clock
   domains).
5. Building the slice without `unit_keys` fails, naming the first scene whose files are not
   preserved; `unit_keys` naming another scene fails.
6. Scene validation, profiling, sample-view building and detection sample loading resolve
   `SourceFileRef`s through the one payload locator; rebuilding writes nothing.

**S5 Mixed sources and M3 verification**

1. A DatasetVersion of the RobotRun `scene-0061` Scene, the nuScenes Scene and the pusht
   Episodes, composed by pins and by source selector, validates and reports both domains and
   sources in its summary.
2. No module downstream of canonical manifests branches on `source_kind` or a format outside
   the payload locator (grep guard).
3. Re-running every S1–S4 step writes no row, artifact or object.
4. The verifier passes on S1–S4 output and every negative control fails it; the result is a
   `docs/history/` record, and only then are the slice golden expectations committed.

**S6 Native nuScenes labels**

1. `IMPORT_LABELS {snapshot_id}` of the slice produces one LabelSet of 4,699 labels whose
   coverage is 39 anchors `(content, content_id, LIDAR_TOP, nuscenes.timestamp,
   sample.timestamp)` with `group_ref = sample.token`, recomputed independently from
   `sample` and `sample_annotation`. The Job result names the snapshot and its content.
2. Without building any sample view, every anchor resolves on the native Scene manifest to
   exactly one `LIDAR_TOP` observation inside the keyframe group whose `native_ref` is its
   `group_ref`. A 1 ns anchor shift, and a `group_ref` swapped with another sample's, each
   fail the verifier.
3. The resolved labels do not depend on the sample-view policy. For sample views anchored on
   `CAM_FRONT` and on `LIDAR_TOP`, attached plus unattached labels is 4,699 in each, and the
   LabelSet bytes are the same.
4. RobotRun labels from `render-labels` carry the new anchor without `group_ref`, and the
   journeys pass.

### Risks

- **Video decoding.** AV1 decoding in the worker image is unverified. M3 stores frame
  selectors and does not decode; the first decoder is an M4 consumer.
- **New Episode surface without consumers.** `frame_timeline`, `component_names` and
  `transition` streams have no consumer in M3. Alignment and the exports refuse
  content-sourced Episodes (D14). M4 must handle them, and any event view it derives from a
  `transition` stream needs a declared rule: pusht's `next.done` is true on two frames per
  Episode.
- **Storage.** The full mini snapshot is 5.05 GiB with the mini scenes' CAN extracts (not
  9.4 GB: 4.63 GB of the raw tree is the CAN expansion of every nuScenes scene). M3 uses the
  0.48 GiB `scene-0061` slice; the full snapshot waits for M5 and more VM space.
- **Slice identity.** The slice is its own content, so its unit ids differ from the full
  snapshot's; M5 regenerates the contract for the full snapshot.
- **Network.** CI and the clean room read pinned local copies, never the HF hub.
- **Licences.** nuScenes is non-commercial and stays a local, git-ignored copy; pusht is MIT.

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
- **Recording clock origins.** Every recording clock has origin `source`, and no build
  config can change it (OQ-9), so RobotRun clocks never align across runs. A consumer that
  needs that adds an explicit `unix_epoch` build-config assertion; asserting it wrongly
  would make clocks from different RobotRuns look comparable.

## 6. M5: E2E & Cleanroom Verification

**Goal.** Prove both ingress families end to end from preserved inputs on an empty runtime,
then make the active documentation describe the new system.

### Work items

- **Carried over from M2** ([M2 integration verification](./m2-integration-verification.md)):
  the full 10-fixture Streaming Acquisition, the joint verification and the clean room,
  deferred for Docker VM capacity.
- **Reference corpus.** Add the nuScenes mini snapshot (5.05 GiB with the mini scenes' CAN
  extracts; M3 uses only the `scene-0061` slice) and the pinned `pusht` revision as
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
