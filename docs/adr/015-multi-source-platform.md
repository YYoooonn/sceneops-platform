# ADR-015: Multi-source platform

## Status

**Accepted (2026-10-16) — not yet implemented.** Written and reviewed in M1. The
implementation is planned in
[Multi-source implementation plan](../history/multi-source-implementation-plan.md) (M2–M5).
Until each milestone lands, [`docs/architecture/`](../architecture/overview.md) keeps
describing the system as it is. Evidence:
[Multi-source architecture audit](../history/multi-source-architecture-audit.md) and
[Multi-source dataset probe](../history/multi-source-dataset-probe.md), both at HEAD
`bfd077e` on `refactor/multi-source-platform`.

Seven review decisions are folded into this text. Each is decided, not open:

```text
R1  DatasetVersion is immutable from creation; composition is staged inside a Job and
    committed in one PostgreSQL transaction; artifact persistence is separate and
    precedes it; no persistent draft DatasetVersion exists                     §7
R2  SourceSnapshot, canonical unit, unit revision and DatasetVersion have separate
    identity semantics; source content identity is separate from snapshot
    (declaration) identity, so identical bytes deduplicate without losing the
    provenance of each upstream revision                                        §3, §5
R3  Snapshots preserve original bytes and provenance; source preservation is not
    canonical conversion                                                        §5, §6
R4  Temporal processing preserves clock-domain semantics, including relative
    timestamps without absolute time                                            §6.2, §8
R5  The golden contract is regenerated from the new logic and verified independently
    against raw inputs; newly generated output is never accepted as its own reference.
    No backward compatibility: nothing is compared with the previous contract     §12
R6  Content identity is not execution provenance: units bind to SourceContent, while
    every import and build operation records the specific SourceSnapshot it used   §4, §6.7
R7  Artifact durability and integrity are established by revision registration, which
    verifies the stored bytes, before any composition; a foreign key to a revision
    row proves registration happened, not that bytes are durable              §6.4, §7
```

**Amended (2026-10-22, M3.0).** OQ-1, OQ-2, OQ-3, OQ-5, OQ-6, OQ-9 and OQ-10 are decided
(§16) on the evidence of
[Native dataset integration architecture](../history/multi-source-native-integration-architecture.md),
read from real LeRobot v3.0 and nuScenes v1.0-mini data at HEAD `4b11e5e`. The decisions
change the text of §6.2 (LeRobot time, recording clock origin), §6.4 (content files in the
registration gate), §6.5 (media types of preserved files, inline values), §6.6 (frame
timeline, component names, transition streams), §6.7 (unit selection), §9 (label anchor),
§14 (rejected alternatives) and §15. OQ-7 and OQ-8 stay open. R1–R7 are unchanged.

**Amended (2026-10-26, M3 S3).** The Episode changes of §6.6 ship as
`sceneops.episode_manifest/v3`, not v2: v2 already names the S2 manifest that added
`clock_domains[]` on the recording path, and a structural change takes a new version
rather than redefining a published one. SceneManifest stays v3; its content variant
lands with the nuScenes builder (S4) without a version change unless its structure
changes. The D14 refusal is decided by what a consumer cannot represent (a
`SourceFileRef` payload, a `transition` stream), not by source kind, so MS-10 holds.

**Amended (2026-10-28, M3 S4).** The Scene changes of §6.6 ship as
`sceneops.scene_manifest/v4`. The content variant changes the structure of v3, which
S2 published: `lineage.source` becomes the §4 union, observation payloads the §6.5
union, and observations and keyframe groups gain `native_ref`. By the rule applied in
S3, a structural change takes a new version, so "without a version change" above does
not hold for Scenes. The loader reads v4 only (no backward compatibility); recording
Scene manifests gain `native_ref: null` and the recording Scene builder's semantics
version moves to 5. Within one Scene a `native_ref` names at most one observation and
at most one group. Scene consumers that cannot represent a `SourceFileRef` refuse it
by capability, as D14 does for Episodes (the analytical observation table); the
payload locator is the one kind dispatch.

This ADR supersedes, in
[ADR-007](./007-canonical-ingestion-architecture.md) Amendment A4, decisions 1, 3, 4 and 5 of
§29.2 and invariants I-31 and I-36. It amends I-1, I-2, I-10, I-16 and I-17 and retires
I-12 (§13). It changes nothing in ADR-008 to ADR-014: acquisition, streaming and execution
reliability stay as they are.

Throughout:

```text
CURRENT    what the code does at bfd077e (see the audit)
PROPOSED   what this ADR decides; unlabelled normative text is PROPOSED
OPEN       not decided here; listed in §16 with a recommendation
```

## 1. Context

SceneOps is moving from a RobotRun-centric acquisition platform to a multi-source robotics
data platform. Robot acquisition stays a first-class source, and external datasets
(nuScenes, LeRobot) become first-class sources of canonical units without passing through a
RobotRun.

ADR-007 A4 made a RobotRun recording the only ingress and moved external datasets into a
tool that converts them to MCAP. It stated the condition for undoing that (§29.9): "a new
amendment readmits it, with a concrete use case that acquisition cannot serve." The probe
supplies two:

- **LeRobot.** A LeRobot episode is already a synchronous, fixed-fps frame table with
  episode-relative float32 time and frames inside shared MP4 files. There is no
  asynchronous recording to acquire. Converting it to MCAP would synthesize message timing,
  receive times, an absolute clock and a RobotRun that never existed. The inbound path A4
  assigned to the tool was never built.
- **nuScenes.** The conversion drops all 1,263 radar readings of `scene-0061`, five of eight
  CAN extracts, the 39 keyframe groups and every native token, and it adds two synthetic
  mission events. The structure is gone before canonicalization and cannot be recovered
  from the RobotRun. The bytes exist three times.

A4's arguments against direct ingestion (two producers per domain, format knowledge in
core, a branch per source downstream) remain valid risks. This ADR accepts the first as the
price of fidelity. It constrains the other two structurally (§10, §11).

## 2. Decision

1. **Two ingress families.** *Acquisition* (live or recorded robot data → capture /
   publication → `REGISTER_ROBOT_RUN` → RobotRun) is unchanged. *Dataset Integration*
   (external dataset → native snapshot → `REGISTER_SOURCE_SNAPSHOT` → SourceContent +
   SourceSnapshot) is added. Neither passes through the other.
2. **Four canonical models.** RobotRun (acquisition session), Scene (spatio-temporal
   observation unit), Episode (task / action sequence) and DatasetVersion (versioned
   composition). Scene and Episode stay sibling domains that share infrastructure, not a
   model. There is no generic data unit.
3. **RobotRun is a source, not a parent.** Every Scene and Episode has exactly one *source
   binding*: a segment of a registered RobotRun recording, or a unit of registered
   SourceContent (§4).
4. **Preservation and canonicalization are separate layers.** SourceContent preserves the
   original bytes, and each SourceSnapshot preserves one upstream declaration of where those
   bytes came from. Neither interprets anything. Canonicalization is a separate,
   reproducible interpretation of pinned content. Nothing converts preserved content to
   MCAP or any other common physical format (§5, §6).
5. **Canonical metadata is separate from physical representation.** A canonical manifest
   holds domain metadata, normalized source timing and *references* to physical data. The
   bytes stay in the representation they arrived in: a recording, an extracted payload or a
   native snapshot file (§6.5).
6. **Separate identities.**
   - SourceContent is identified by its bytes.
   - A SourceSnapshot is identified by its upstream declaration and bound once to one
     content.
   - A canonical unit is identified by its source (a RobotRun or a content) and unit key.
   - A unit revision is identified by its manifest content.
   - A DatasetVersion is identified by its name, bound once to an immutable composition
     (§3).
7. **A DatasetVersion is immutable from creation.** A Job stages the composition and commits
   it in one PostgreSQL transaction. Every artifact the composition references is persisted
   before it and separately. No empty, partial or draft DatasetVersion is ever persisted
   (§7).
8. **Clock domains are explicit.** Builders normalize timestamps to integer ns in a declared
   clock domain, which may be absolute or relative. One source-independent alignment
   capability turns pinned streams and an explicit policy into an aligned data view, within
   one clock domain, for RobotRun, Scene or Episode scopes. It never invents an absolute
   time (§6.2, §8).
9. **Derived resources stay derived.** Alignment results, ScenarioSet, EpisodeSet, LabelSet
   and LearningDataExport pin the revisions they consume. They are never identity or
   membership (§9).
10. **Format knowledge is confined** to the SourceContent / SourceSnapshot records and to
    per-format readers and builders in capability packages. No format identifier enters unit identity, and nothing
    downstream branches on one (§10, MS-13).
11. **The acquisition simulator stays.** `integrations/dataset-acquisition` keeps producing
    MCAP and ROS 2 replay to exercise acquisition and streaming. It is no longer how external
    datasets become canonical data.

## 3. Domain model and identity semantics

```text
Robot 1 ──< RobotRun                         acquisition identity (unchanged)
SourceSnapshot >── 1 SourceContent           upstream declarations (provenance) of one
                                             set of preserved bytes (new)

             source binding (exactly one per unit)
RobotRun ──────┐
SourceContent  ┴──< Scene   1 ──< SceneRevision     (manifest + projection)
               └──< Episode 1 ──< EpisodeRevision   (manifest + projection)

Dataset 1 ──< DatasetVersion 1 ──< member ──> exactly one SceneRevision or EpisodeRevision

derived (pins revisions; never identity, never membership)
  LabelSet ── anchors on (source, channel, clock domain, timestamp)
  SceneSampleView ── SceneRevision (+ LabelSet revisions)  ──< ScenarioSet
  AlignedEpisode  ── EpisodeRevision                       ──< LearningDataExport
  EpisodeSet      ── EpisodeRevisions
```

The identities answer different questions. None of them embeds another's mutable choice:

| Identity | Answers | Derived from | Excludes | New identity when |
| --- | --- | --- | --- | --- |
| **SourceContent** `content_id` | which exact native bytes | `f(content_schema, content_checksum)`; `content_checksum` = sha256 of the canonical file list `[(path, size_bytes, sha256)]`, paths relative to the source root | format, upstream identity and revision, release, licence, selection rules, storage location, time | any file byte, path or selected file changes |
| **SourceSnapshot** `snapshot_id` | which upstream declaration (dataset, revision, selection), bound to which bytes | `f(snapshot_schema, format, upstream, upstream_revision, selection_checksum)`; `upstream_revision` is the source's content revision (an HF commit) or, where the source has none, its release name (`v1.0-mini`) | the bytes themselves (it *references* one `content_id`), descriptor annotations such as licence text (§5), retrieval facts | a different upstream dataset, revision or selection is declared |
| **Canonical unit** `unit_id` | which logical unit of which source | `f(unit_id_schema v2, domain, source_kind, source_id, unit_key)`; `source_id` = `robot_run_id` or `content_id` | DatasetVersion, SourceSnapshot, producer, build config (except a recording's segmentation policy, §6.3), revision | the source bytes change (other content, another RobotRun), or the unit boundary changes |
| **Unit revision** `(unit_id, manifest_checksum)` | which canonical interpretation of that unit | sha256 of the canonical manifest bytes; `manifest_artifact_id` is a deterministic function of `(unit_id, manifest_checksum)` | DatasetVersion, execution context | the producer fingerprint changes (channels, semantics version, normalization rule) |
| **DatasetVersion** `(dataset_id, version)` | which named, frozen composition | a human name, bound once to `composition_checksum` = sha256 of the sorted member pins `[(domain, unit_id, manifest_checksum)]` | anything mutable; source location or format | always a new name; a name is never rebound |

Consequences of the separation:

- **Units follow bytes, snapshots follow declarations.** Each case below is decided by
  `content_id` and `snapshot_id` alone:

  | Case | SourceContent | SourceSnapshot | Units |
  | --- | --- | --- | --- |
  | new upstream revision, identical selected bytes | reused (deduplicated) | new record, bound to the existing content | unchanged: same `unit_id`s; a rebuild with the same config is a no-op |
  | new upstream revision, different bytes | new | new | new units, not revisions of old ones |
  | same declaration re-registered with the same bytes and descriptor | reused | no-op | unchanged |
  | same declaration with different bytes | — | **conflict, fails** | — |
  | same declaration and bytes, different descriptor annotations | — | **conflict, fails** (OQ-10) | — |
  | same bytes declared under a different format | — | **conflict, fails** | — |
  | two different selections that select identical files | reused | two records | unchanged |

- **Provenance is never lost to deduplication.** Every declaration is a separate, immutable
  SourceSnapshot. A unit's provenance is its content plus every snapshot that declares
  that content (one or more). Canonical builds are a function of bytes (MS-4), so binding
  units to content, not to one declaration, is the accurate lineage.
- **Correspondence across different bytes** (the same nuScenes scene in `v1.0-mini` and
  `v1.0-trainval`, if their bytes differ) is a separate, derived relation, out of scope
  here. It never enters unit identity.
- **A revision is determined by its unit and producer fingerprint.** For one unit and one
  fingerprint exactly one manifest checksum may exist. A second checksum means a
  non-deterministic producer and fails loudly.
- **Two DatasetVersions may have equal compositions** under different names. A name is
  never rebound to a different composition.

| Relationship | Rule |
| --- | --- |
| Scene ↔ Episode | independent; neither references the other; either may come first |
| RobotRun → Scene / Episode | 0..*; a RobotRun can source both domains (as today) |
| SourceSnapshot → SourceContent | n..1; every registered content has at least one snapshot (they are registered together) |
| SourceContent → Scene / Episode | 0..*; a domain builder for that format must exist (nuScenes → Scene and LeRobot → Episode in this ADR) |
| Unit → revision | 1..*; revisions are immutable and never deleted by registration |
| DatasetVersion → member | 0..*; at most one revision per unit; members may mix domains and sources |
| RobotRun / SourceContent / SourceSnapshot → DatasetVersion | never a member (I-6 kept) |

## 4. Source bindings

The source binding is the lineage block of a canonical manifest: a closed, discriminated
union. It is provenance only and carries no Scene-, Episode- or format-specific field.

```text
RecordingSegmentSource   (CURRENT, unchanged)
  source_kind = "recording"
  robot_run_id, recording_artifact_id, recording_checksum
  source_clock, [start_timestamp_ns, end_timestamp_ns), unit_key

ContentUnitSource        (PROPOSED; shape from ADR-007 §27.2 / §28)
  source_kind = "content"
  content_id             the registered SourceContent
  content_checksum       pins the exact native bytes
  source_clock, [start_timestamp_ns, end_timestamp_ns)
  unit_key               the source's own unit key, verbatim
```

Source revision (the fingerprint's source input, as in ADR-007 §27.7):

```text
RecordingSourceRevision   robot_run_id, recording_checksum              (unchanged)
ContentSourceRevision     content_id, content_checksum                  (new)
```

The binding names the content and pins its bytes. It names no SourceSnapshot and copies
no descriptor. Upstream dataset names, revisions, releases and licences are reachable
through `content_id` → snapshots, and they are never serialized into a unit manifest
(I-18, I-30). So registering another declaration of the same bytes changes no manifest,
fingerprint or unit id. The binding also excludes URIs, roots, DatasetVersion, job and
pipeline ids, and execution timestamps.

**Execution provenance is separate and is kept.** "Which declaration did this operation
use" is execution history, not content identity. It is recorded on the operations, never
in a manifest:

- `REGISTER_SOURCE_SNAPSHOT` and every snapshot pipeline Job carry the `snapshot_id` in
  their params and record `snapshot_id` and `content_id` in their result.
- The manifest ArtifactRecord a build publishes carries that build's `job_id` and
  `pipeline_run_id`. This uses the existing producer lineage columns of `artifacts`
  ([Jobs and pipelines](../architecture/jobs-and-pipelines.md) §7).

The chain revision → manifest ArtifactRecord → Job → `snapshot_id` names the snapshot of
the build that first produced the revision. A later build of the same bytes through another
snapshot records its own `snapshot_id` in its own Job result and reuses the revision.
Content-level provenance answers "which declarations exist for these bytes". Execution
provenance answers "which declaration did this build read".

## 5. SourceContent and SourceSnapshot: source preservation

Source preservation has two records with different jobs:

- **SourceContent** preserves the original bytes.
- **SourceSnapshot** preserves one upstream declaration of where those bytes came from.

Neither interprets anything. Neither decides a unit boundary, clock, channel meaning or
value decoding, and neither depends on a canonical builder supporting the format. For
Dataset Integration they play the roles the recording bytes and the RobotRunManifest play
for Acquisition: pinned bytes plus the verified resolver (content), and the declared
provenance (snapshot).

```text
inventory (canonical JSON, write-once)                  ── SourceContent, identity-defining
  schema_version       sceneops.source_content_inventory/v1
  files[]              path (relative to the source root, verbatim), size_bytes, sha256;
                       sorted by path
  content_checksum     sha256 of the canonical files[] list
content_id = f(content_schema, content_checksum)

declaration + descriptor (canonical JSON, write-once)  ── SourceSnapshot
  declaration (identity-defining)
    format             open identifier (nuscenes, lerobot)       ADR-007 §27.6 rules
    upstream           the source's own dataset identifier ("lerobot/pusht", "nuscenes")
    upstream_revision  the source's content revision (HF commit sha) or, where it has
                       none, its release name ("v1.0-mini")
    selection          the include rules that chose files[] from the source tree, so what
                       was *not* preserved is explicit; selection_checksum = sha256 of
                       their canonical form
  content_id           the bytes this declaration names
  annotations          licence and other source-declared notes (provenance, not identity)
snapshot_id = f(snapshot_schema, format, upstream, upstream_revision, selection_checksum)

source_contents (PostgreSQL)
  content_id, format, content_checksum, inventory_artifact_id (FK artifacts, RESTRICT),
  file_count, total_bytes, registered_at;                no status, never updated
source_snapshots (PostgreSQL)
  snapshot_id, content_id (FK source_contents, RESTRICT), format, upstream,
  upstream_revision, selection_checksum, descriptor_artifact_id (FK artifacts, RESTRICT),
  descriptor_checksum, registered_at;                    no status, never updated
```

`format` is a declaration, recorded on the snapshot and fixed on the content by its first
registration. A later declaration of the same bytes under another format is a conflict.
The format's own *version* (nuScenes `v1.0-mini` tables, LeRobot `codebase_version`) is
not declared at all: the format reader derives it from the bytes. A canonical build
therefore never reads a descriptor, which keeps MS-4 true when several snapshots share one
content.

Preservation rules:

- Files are stored byte-for-byte at `{source_content_root}/{content_id}/files/{path}`,
  write-once, once per content however many snapshots declare it. Paths are kept
  verbatim. Nothing is renamed, re-encoded, decompressed, transcoded or filtered inside a
  file.
- Every selected file is preserved, including files no builder reads (licence, README,
  maps, CAN extracts). Preservation is not limited to what canonicalization needs today.
- The inventory is stored once per content. Each descriptor is stored at
  `{source_snapshot_root}/{snapshot_id}/descriptor.json`, write-once, so a second,
  different descriptor for the same declaration is refused by storage as well as by
  registration.
- Retrieval facts (local path, URL, time, operator) are execution history of the
  publication. They go to the publication's logs and Job record, not into content or
  snapshot.

Lifecycle, mirroring recording publication and `REGISTER_ROBOT_RUN`:

```text
publish-snapshot (DB-free, apps/publisher)
  hash the selected local files → inventory → content_id;  declaration → snapshot_id
  upload each file under content_id, write-once (bytes already there are reused:
    deduplication; different bytes at the same key fail)
  write the inventory (identical → reused)
  write the descriptor last                      no descriptor = snapshot not published
REGISTER_SOURCE_SNAPSHOT (worker Job)
  verify the descriptor (strict, canonical bytes) and the inventory
  if the content is not registered: verify every file (size, sha256)
  if it is: its files were verified at its first registration and are write-once;
            verify the inventory bytes against the registered content_checksum
  one PostgreSQL transaction:
    content   absent → insert (with its inventory ArtifactRecord)
              present, same format → reuse;  present, other format → fail
    snapshot  absent → insert (with its descriptor ArtifactRecord)
              present, same content_id and descriptor_checksum → no-op
              present, different content_id → fail  (one declared revision, two byte sets)
              present, different descriptor  → fail  (one declaration, two descriptors)
```

The unique keys on `content_id` and `snapshot_id` decide concurrent registrations. The
loser re-reads and applies the same rules, so identical work converges and inconsistent
work fails. A content row never exists without a snapshot, because both are inserted in
the same transaction.

Registration verifies preservation only. Whether a canonical builder supports the format
version found in the bytes is decided by that builder, which fails loudly at build time. A
snapshot of a format no builder supports can still be registered and preserved.

Failure semantics:

- A crash before the inventory leaves files that no inventory names. These are orphan
  candidates under the content prefix, classified like `robot_runs/` today.
- A crash after the descriptor but before registration is recovered by re-running
  registration.
- A retry converges, because ids and keys are derived from bytes and declarations.

## 6. Canonical units: canonicalization

Canonicalization interprets a pinned source: a RobotRun recording or a SourceContent. It
decides unit boundaries, clock domains, timestamp normalization, channel and stream meaning,
and value decoding. Its output is reproducible from the pinned source, the producer
semantics and the build configuration (MS-4). The canonical manifest is an interpretation.
It is never a substitute for the preserved source and never its preservation format.

### 6.1 Identity

See §3. DatasetVersion leaves unit identity. The same unit in two DatasetVersions is one
unit, referenced twice.

### 6.2 Clock domains and timestamp normalization

Every canonical timestamp is an integer ns value in one declared **clock domain**. Manifests
declare their clock domains:

```text
ClockDomain
  clock           open identifier (existing rule; external clocks are "<format>.<clock>")
  origin          unix_epoch   absolute; comparable wherever the same clock id is used
                  source       absolute within one source (one RobotRun / one content);
                               epoch unknown or not asserted
                  unit         relative; zero is the unit's own origin; no absolute anchor
  resolution_ns   the source's native resolution (1,000 for µs sources), informative
```

**Comparability.** Two timestamps are comparable only if they have the same clock id and
the same origin instance: any instance for `unix_epoch`, the same source for `source`, the
same unit for `unit`. Two LeRobot episodes both on `lerobot.frame_time` are not
comparable with each other.

| Source | Clock domain | Normalization |
| --- | --- | --- |
| RobotRun recording | per channel as declared by `build_config` (`header_stamp`, `log_time`, …); origin `source` for every clock, set by the recording builders, not configurable (OQ-9) | CURRENT rule, unchanged values |
| nuScenes sensors and ego poses | `nuscenes.timestamp`, origin `unix_epoch`, resolution 1,000 ns | integer µs → ns by `promote_to_ns`, exact. All 31,206 `ego_pose` timestamps equal their `sample_data` timestamps (probe §2), so one clock is justified |
| nuScenes CAN | not canonicalized in M2–M5 (preserved in the snapshot only). If it is canonicalized later, it gets its own clock id until a shared hardware clock with the sensors is verified | — |
| LeRobot | `lerobot.frame_time`, origin `unit`, resolution 1 ns | `t_ns = round_half_even(frame_index · 10⁹ / fps)`, with `fps` declared once on the Episode's frame timeline (§6.6). An occurrence's frame index is recovered exactly from `t_ns`, so the recorded float32 `timestamp` and `frame_index` are not kept as values. The builder rejects a dataset whose `timestamp` is not `float32(frame_index / fps)` on every row instead of snapping it to a grid (OQ-2) |

Rules:

- Normalization changes representation (unit, integer form), never the domain. No builder
  adds an epoch to a relative time, shifts a clock or merges clocks.
- Windows are half-open in one declared clock domain.
  - nuScenes: from the first to the last sensor reading the source assigns to the scene's
    samples, plus 1 ns.
  - LeRobot: `[0, t(length − 1) + 1)` in the unit's own origin.
- A timestamp in another domain is never compared with a window.
- **A sampling rate is not a clock property.** A clock domain says what zero means and which
  timestamps are comparable. A frame rate says how one unit sampled its clock, and it is
  declared on that unit's frame timeline (§6.6), never on the clock domain.

### 6.3 Unit keys

- Recording: the segmentation policy is part of the key (`<policy>/<k>`, e.g.
  `fixed_duration-10000000000ns/3`). A different segmentation therefore yields different
  units, not revisions of misaligned ones.
- SourceContent: the native unit key, verbatim (nuScenes `scene.token`, LeRobot
  `episode_index`). It is read from the bytes, so every snapshot of the same content
  yields the same keys (OQ-5). The LeRobot key is `str(episode_index)`, decimal without
  padding. `scene.name` is display data and enters no identity or manifest.

### 6.4 Revisions and registration

A revision is one registered manifest, `(unit_id, manifest_checksum)`, with its
`manifest_artifact_id`, `producer_fingerprint` and the searchable projection that today lives
on SceneRecord / EpisodeRecord.

```text
REGISTER_SCENES / REGISTER_EPISODES (input: manifest artifact ids of one build)
  verify    the durability and integrity gate below, and the source binding against the
            registered source
  apply     one PostgreSQL transaction: insert unit if absent; insert revision if absent
            same unit + same manifest_checksum          → no-op
            same unit + same fingerprint + other checksum → fail (non-deterministic producer)
  never     deletes or repoints a revision, touches a DatasetVersion, or moves a "current"
```

**Durability and integrity gate.** A revision row is written only after registration has
verified, against the ArtifactStore itself and not only against database rows, that
everything the manifest needs is stored with the bytes the manifest pins:

```text
manifest        ArtifactRecord kind, checksum and size; bytes read back from the store,
                checksum + size + strict canonical parse                     CURRENT
artifact refs   each ArtifactPayloadRef resolves to an ArtifactRecord with exactly its
                checksum, size and media type                                 CURRENT
                and the object is present in the store with that size        NEW
                (one listing per payload prefix; CURRENT checks records only)
source refs     each SourceFileRef matches the content inventory entry (path, size, sha256),
                the content is registered (its files were fully hashed then), and the
                file object is present in the store with its inventory size
                (one listing per content prefix)                              NEW
payload bytes   hashed by read-back when published (write_once,
                ObservationPayloadStore) or when content was registered, and re-verified
                on every pinned read                                          CURRENT / NEW
```

So a revision row means "registered, and its bytes were present and checksum-verified at
registration". Every later reference relies on that row, and nothing after registration
rewrites or deletes bytes: keys are write-once and no code path deletes a referenced
object ([limitations](../architecture/limitations.md) §3). A database foreign key alone
proves none of this; the gate does.

There is no "current revision" pointer on a unit. This is consistent with derived artifacts
([limitations](../architecture/limitations.md) §3). Quality runs, readiness and every
consumer address a revision. The scope rule "one fingerprint per
`(DatasetVersion, robot_run_id)`" (ADR-007 §18.3, I-12) is retired: it existed to keep a
mutable DatasetVersion consistent.

### 6.5 Payload references

```text
PayloadRef (union)
  ArtifactPayloadRef   CURRENT PayloadRef: OBSERVATION_PAYLOAD artifact id + sha256 + size +
                       media_type (recording-derived payloads, unchanged)
  SourceFileRef        content_id, path, sha256, size_bytes, media_type,
                       frame? {timestamp_ns}   frame selector within a native video, in
                                               the video's own time base
```

- nuScenes JPEG, `.pcd.bin` and `.pcd` readings are referenced as whole preserved files.
  No payload copy is extracted.
- LeRobot numeric features are decoded into occurrence values, as recording-derived
  Episodes already are. This is canonicalization, not preservation, and it must be exact:
  float32 values serialize as the decimal of their exact float64 widening and read back
  bit-identically. Images are `SourceFileRef`s with a frame selector into the preserved
  MP4. Values stay inline (OQ-3); no native column reference type exists.
- The inventory is format-independent and records no media type. The format-specific
  builder assigns `media_type` from source-format evidence and fails when that evidence
  disagrees: nuScenes from the channel's `sensor.modality` and the file suffix (its
  `fileformat` is `pcd` for both lidar and radar), LeRobot from the feature's `video` dtype
  and the `.mp4` suffix. The media type is part of the manifest and its checksum.
- Resolution verifies the content inventory entry's checksum and size, then reads from the
  content store. Content files are SceneOps-owned, so I-24 holds.

### 6.6 Manifest changes

SceneManifest v3 and EpisodeManifest v2 make these changes:

- `lineage.source` becomes the §4 union.
- `clock_domains[]` declares every clock the manifest uses (§6.2).
- Payloads become the §6.5 union.
- Observations, keyframe groups and occurrences gain an optional verbatim `native_ref`
  (nuScenes `sample_data.token` / `sample.token`, LeRobot global `index`).
- An Episode sampled on a frame grid declares one optional `frame_timeline {clock, fps,
  frame_count}`. `fps` is stored there once. Every occurrence on that clock lies at
  `round_half_even(k · 10⁹ / fps)` for exactly one frame index `k` in `[0, frame_count)`, and
  the window is `[0, t(frame_count − 1) + 1)`. Recording Episodes are asynchronous and
  declare none.
- A selected Episode field gains an optional `component_names`: the source's names of a
  vector value's dimensions, in source order. Every value of that field then has exactly that
  length.
- Episode streams gain the role `transition`: a per-frame signal about the step from a frame
  to the next (LeRobot `next.*`). It has one occurrence per frame, at the frame the source
  records it on, with values verbatim in their source type. The manifest derives no event
  from it; an event view is derived downstream only where its meaning is unambiguous.

No membership, status, location or snapshot descriptor enters a manifest.

### 6.7 Builders and pipelines

There is one builder per (source format, domain). Each is a pure function of pinned source
content, producer semantics and normalized build configuration (I-32 generalized):

```text
RECORDING_SCENE_BUILDING     robot_run_id + build_config → Scenes        (CURRENT, no DatasetVersion params)
RECORDING_EPISODE_BUILDING   robot_run_id + build_config → Episodes      (CURRENT, no DatasetVersion params)
SNAPSHOT_SCENE_BUILDING      snapshot_id + build_config → Scenes         nuScenes builder (sensors only)
SNAPSHOT_EPISODE_BUILDING    snapshot_id + build_config → Episodes       LeRobot builder
```

Each runs build → register → validate → profile (optional), as today. A build that yields
no unit fails (unchanged).

- **What a snapshot pipeline reads.** It takes a `snapshot_id` because operators select
  declarations. It resolves that to its `content_id` once, records both in the Job
  result as execution provenance (§4), and builds from the content alone.
- **Same bytes, second snapshot.** Building through another snapshot of the same content
  with the same config produces the same manifests and is a no-op at registration.
- **Format support.** A builder rejects a format, or a format version found in the bytes,
  that it does not support.
- **Unit selection.** A snapshot pipeline takes an optional `unit_keys`. It is execution
  scope, not build configuration, so it never enters the producer fingerprint. A manifest
  depends only on its own unit, so building a subset yields the same revisions. A selected
  unit whose files are not in the inventory fails the build; it is never skipped.

## 7. DatasetVersion

A DatasetVersion is immutable from creation. There is no draft DatasetVersion, no empty
DatasetVersion and no status that means "not finished". If its row exists, it is complete.

```text
COMPOSE_DATASET_VERSION (Job; submitted through the Jobs API or a dataset route that only
                         creates and dispatches the Job)
  params    dataset_id, version name, and either the complete list of member pins or a
            selector
  stage     inside the Job, in memory or in job-scoped execution state only:
              resolve the selector to pins (once)
              verify every pin names a registered revision; at most one revision per unit
              compute summaries and composition_checksum
  commit    one PostgreSQL transaction: version row + all member rows + summaries +
            the resolved selector; nothing else is written in it
  converge  same (dataset_id, version) + same composition_checksum   → no-op
            same (dataset_id, version) + different composition_checksum → fail
  result    the committed version; never modified afterwards
```

```text
dataset_version_members
  dataset_version_id   FK dataset_versions (RESTRICT)
  domain               scene | episode
  scene_revision / episode_revision
                       FK to the revision row (exactly one, by domain; CHECK), RESTRICT
  unit_id, manifest_artifact_id, manifest_checksum    the pin, as in SceneRevisionRef
  unique (dataset_version_id, unit_id)
```

Rules:

- **The atomic unit is a PostgreSQL transaction, and only that.** The composition's
  atomicity claim covers database rows: the version row, its members, its summaries and
  the resolved selector. It covers no object-store write.
- **Artifact persistence is separate and comes first.** Composition writes no artifact.
  Before any composition, three things already exist:
  - the manifest objects and ArtifactRecords, written by the builders;
  - the payload objects and content files they reference;
  - the revision rows.

  Durability and integrity of those bytes are guaranteed by revision registration's gate
  (§6.4), which verified them in the store before writing the revision row, and by
  write-once keys with no deletion path afterwards. A member's foreign key to the revision
  row only proves the revision passed that gate. Composition depends on the gate, not on
  the foreign key. Composition also re-checks each pin against the revision row (unit,
  manifest artifact id and checksum must match exactly) inside its staging step, so a pin
  that names no gate-verified revision is rejected before commit.

  If an exported composition manifest is ever added, it is a derived artifact. It is
  written write-once at a checksum-qualified key, outside the transaction, and it is never
  part of the atomicity claim. Such a manifest without a committed version is an orphan
  candidate. A committed version without one is complete, and the manifest is re-derivable
  from its rows.
- **Nothing persists a partial version.** Staged state lives in the Job only. A crash or
  failed verification before commit leaves no DatasetVersion row. A retry re-stages from
  the Job params. Job params and results are execution records, committed by `JobRunner`
  in its own transactions. They are not readable as a DatasetVersion and are never resolved
  as one.
- **Job state and composition are separate commits.** If the worker dies after the
  composition commit but before `JobRunner` records success, the Job is reclaimed (ADR-010)
  and re-run. The re-run finds the same name with the same `composition_checksum` and
  converges as a no-op. A reclaimed Job's domain write is not fenced, so this convergence
  rule is what keeps duplicate execution safe.
- **Concurrency.** The unique key `(dataset_id, version)` decides a race. The loser re-reads:
  if the committed version has the same `composition_checksum` it converges, otherwise it
  fails. No row lock on the version is needed, because no writer exists after commit.
- **The empty-version endpoint is removed.** CURRENT `POST /datasets/{id}/versions`
  (`apps/api/app/domains/datasets/router.py`) synchronously creates an empty version that
  registrations fill later. That is the persistent draft this ADR excludes. The route either
  disappears or only creates and dispatches a `COMPOSE_DATASET_VERSION` Job.
- **Summaries** (`scene_count`, `episode_count`, observed channels) are computed once,
  inside the composing transaction, from the member revisions' projections.
- A version may mix sources (nuScenes-native Scenes with RobotRun Scenes) and domains.
- A version carries no source location or format (unchanged).
- A revision referenced by a version cannot be deleted (RESTRICT).
- `Dataset` keeps its mutable display metadata and `default_version` pointer. A Dataset is
  a namespace, not versioned content.

## 8. Shared temporal alignment

```text
Data scope + timestamped streams + alignment policy → aligned data view
```

```text
DataScope           one pinned input:
                      RobotRun recording   robot_run_id + recording_checksum + stream selection
                      Scene revision       unit_id + manifest_checksum
                      Episode revision     unit_id + manifest_checksum

StreamExtractor     per scope kind, pure over verified input: DataScope → TimestampedStream[]
                    reads canonical (already normalized) timestamps and their clock domain;
                    makes no temporal decision

TimestampedStream   stream_key (verbatim channel / topic, "#field" for a selected field),
                    clock domain (clock, origin, origin instance), value kind
                    (numeric scalar | numeric vector | reference | other),
                    samples [(timestamp_ns, value, occurrence ref)] in canonical order (I-34)

AlignmentPolicy     timeline   fixed_frequency {hz} over a declared span
                               | anchor_stream {stream_key, stride}
                    per stream association: exact | nearest | previous | linear_interpolation,
                               tolerance_ns, max_gap_ns; explicit, never inferred from names

AlignmentEngine     pure, deterministic: streams + policy → AlignedDataView
                    (clock domain + timeline + per-stream AlignedSignal: status, value,
                     source timestamp, delta, interpolation facts)

Domain projections  AlignedEpisode   = view + observation / action namespaces + task
                    SceneSampleView  = anchor view + members + pose + label attachment
                    RobotRun scope   = extraction + engine only; no persisted resource until
                                       a consumer exists
```

Clock-domain rules:

- **One clock domain per alignment.** All input streams and the timeline share one clock
  id and one origin instance. Anything else is rejected. A clock mapping between domains
  would be a separate, explicit derived input; it is out of scope here.
- **Relative stays relative.** For a `unit` origin the timeline, every aligned timestamp
  and every output field are relative to that unit's origin. The engine, the domain
  projections and every export carry the clock domain with the timestamps. None of them
  adds, infers or defaults an absolute anchor.
- **No cross-unit alignment on a relative clock.** Two units' relative timelines are never
  merged, ordered against each other or given a common origin.
- **Ticks are computed, not accumulated.** Tick `k` of a fixed-frequency timeline is
  `origin + round_half_even(k · 10⁹ / hz)` in the timeline's domain. This is the same rule
  as LeRobot normalization, so aligning a source at its native rate resolves exactly at every
  fps. CURRENT `generate_fixed_frequency_timeline` rounds the period once to integer µs
  (`quantize_period_us`) and emits `start + n · dt_us`. That drifts from the nominal grid for
  rates that do not divide 10⁶: at 30 Hz, `dt_us` = 33,333, so tick n is n/3 µs early, about
  100 µs after 300 frames.

Other rules:

- **ns end to end.** The engine works in integer ns, and the microsecond flooring of Episode
  alignment ends (alignment semantics version bump). A learning table that stores µs
  declares the projection and keeps the clock domain columns beside it.
- **Interpolation only for numeric values.** References and other kinds use exact, nearest
  or previous.
- **Normalization and alignment never mix.** Builders normalize, extractors read, and the
  engine aligns. Neither alignment nor extraction changes a canonical manifest.
- **Starting point.** The kernels in `sceneops_core.episodes.alignment.policies` move to a
  domain-neutral core module, and both current consumers are rebuilt on it.

## 9. Derived resources

| Resource | Pins | Change |
| --- | --- | --- |
| Alignment results (`AlignedEpisode`, `SceneSampleView`) | one revision + policy | built on §8; recipe key = scope pin + policy + semantics version; carry the clock domain |
| ScenarioSet | sample views | unchanged |
| EpisodeSet | Episode revisions + recorded criteria | new: the Episode-side selection, analogous to ScenarioSet; curation produces one |
| LabelSet | anchors | the anchor becomes `(source_kind, source_id, channel, clock, timestamp_ns)` with `source_id` = `robot_run_id` or `content_id`; a `unit`-origin anchor also names its unit; an anchor may carry `group_ref`, the `native_ref` of the source-defined group it annotates. nuScenes `sample_annotation` imports natively against content-sourced Scenes (OQ-6), anchored on its sample: `LIDAR_TOP` at `sample.timestamp` with `group_ref = sample.token`. The anchor resolves against the Scene manifest's observation and keyframe group, never through a sample view or a camera association |
| LearningDataExport | aligned revisions | unchanged identity; task comes from canonical Episode streams where the source has one; timestamps keep their clock domain |

Each resource records the DatasetVersion it was selected from, when it was, so it can be
reproduced. None is a DatasetVersion member.

## 10. Package and process boundaries

```text
sceneops-core        schemas: source bindings, clock domains, content inventory, snapshot
                     declaration / descriptor, content and snapshot records, PayloadRef union,
                     unit identity v2; temporal: streams, policy, engine (pure)
sceneops-sources     NEW, layer 2 (storage): snapshot publication (DB-free), verified content
                     reader, native readers nuscenes/ and lerobot/ (json + pyarrow; no SDK)
sceneops-scenes      + content (nuScenes) builder → edge to sceneops-sources
sceneops-episodes    + content (LeRobot) builder  → edge to sceneops-sources
sceneops-db          content, snapshot, unit / revision, membership tables
apps/publisher       + publish-snapshot (DB-free; mounts raw sources like acquisition services)
apps/worker          REGISTER_SOURCE_SNAPSHOT, snapshot build handlers, COMPOSE_DATASET_VERSION
integrations/dataset-acquisition   unchanged role: acquisition / replay simulator
integrations/lerobot               unchanged: isolated export runtime
```

A new package and new edges are an architectural change: `tools/checks/import_boundaries.py`
and [Repository structure](../architecture/repository-structure.md) change together. The
native readers duplicate a small part of the acquisition tool's nuScenes table parsing. The
tool imports no SceneOps package (I-36's import rule stays for the tool), and coupling them
would cost more than the duplication.

## 11. Invariants

```text
MS-1   Every Scene / Episode revision has exactly one source binding: a segment of a
       registered RobotRun recording or a unit of a registered SourceContent; every
       registered SourceContent is declared by at least one SourceSnapshot.
MS-2   Canonical identity and provenance contain no location, display name, snapshot
       declaration or descriptor, or execution context (I-18, I-30 kept).
MS-3   Source preservation: SourceContent files are byte-identical to the selected native
       files, stored once per content, write-once, and verified at the content's first
       registration; each SourceSnapshot records one upstream declaration and its
       descriptor; nothing in either is interpreted, and neither record is ever updated.
       Registration never depends on canonical builder support.
MS-4   A canonical build is a function of pinned source content, producer semantics and
       normalized build_config only (I-32 generalized to every source); it never modifies
       its source.
MS-5   Every canonical timestamp is an integer ns value in exactly one declared clock domain
       (clock, origin); normalization never changes the domain, adds an absolute anchor or
       converts between domains.
MS-6   content_id = f(content_checksum); snapshot_id = f(format, upstream,
       upstream_revision, selection_checksum), bound once to one content_id and one
       descriptor; unit_id = f(domain, source_kind, source_id, unit_key) with source_id =
       robot_run_id | content_id; a revision is (unit_id, manifest_checksum); a
       DatasetVersion name is bound once to one composition_checksum. No identity includes a
       DatasetVersion except the DatasetVersion's own, and no unit identity includes a
       snapshot.
MS-6a  Inconsistent declarations fail loudly and change nothing: one snapshot_id with two
       content_ids or two descriptors, or one content_id under two formats. Several
       snapshots sharing one content_id is deduplication, not a conflict.
MS-7   Registering a revision never deletes or changes another revision or a DatasetVersion;
       one unit and one producer fingerprint have at most one manifest checksum; a revision
       row is written only after its manifest and every referenced object were verified
       present and checksum-consistent in the ArtifactStore (§6.4).
MS-8   A DatasetVersion exists only as a complete, committed composition: created in one
       PostgreSQL transaction by one Job, immutable afterwards, exactly one pinned revision
       per member unit; no empty, partial or draft version is persisted. The transaction
       writes rows only; it admits only revisions that passed the MS-7 registration gate,
       and it relies on that gate, never on a foreign key, for artifact durability.
MS-9   Derived resources pin revisions; none is canonical identity or membership.
MS-10  Downstream of canonical manifests no workflow branches on source kind or format,
       except payload resolution, which dispatches on the reference type (I-20 kept).
MS-11  Alignment is derived, runs within one clock domain, never changes a canonical
       manifest and never fabricates absolute time; normalization never aligns.
MS-12  RobotRun stays immutable acquisition provenance and is never a DatasetVersion member
       (I-5, I-6 kept).
MS-13  A format identifier appears only in the snapshot declaration, the content and
       snapshot records, and as the qualifier of a source-defined clock identifier
       (`<format>.<clock>`, the existing `validate_source_clock` rule); it never enters
       content_id or unit identity, and no workflow branches on it.
MS-14  Content identity and execution provenance are separate: units, revisions and
       manifests reference SourceContent only; the SourceSnapshot an import or build used
       is recorded in that operation's Job params and result and reached from a revision
       through its manifest ArtifactRecord's job lineage.
```

## 12. Effect on contract tests

Changing identities invalidates every stored expectation keyed by an id or checksum. The
new logic is built as new, with no backward compatibility: no output, id or behaviour of the
previous contract is kept, compared or migrated, and the previous golden expectations are
discarded. The golden reference contract is regenerated from the new logic and accepted
only through independent verification (R5):

1. **Verify against raw inputs.** The verifier derives its expectations from preserved
   inputs, not from SceneOps output. These are the locked MCAPs, the nuScenes tables and
   files, and the LeRobot Parquet. It implements the documented rules of the new logic
   (identity formulas, canonicalization, clock domains, association and alignment)
   itself, recomputes checksums from bytes and compositions from pins. It may parse
   outputs with the canonical schemas but never calls a builder, registrar or composer it
   is verifying, and its rules are written from the documentation, never fitted to the
   output.
2. **Negative controls.** Each run injects known faults and requires the verifier to fail:
   a dropped observation, a 1 ns shift, a changed payload byte, a wrong pin, a relative
   timestamp given an absolute anchor.
3. **Record.** The verification result is kept as a point-in-time record in
   `docs/history/`. Only then are the new golden expectations committed.

## 13. Effect on ADR-007

| ADR-007 | Effect |
| --- | --- |
| §29.2 decisions 1, 3, 4, 5; I-31; I-36 (as a platform rule) | superseded by §2 |
| §29.2 decisions 2, 6–10; I-32–I-35, I-37 | kept for recordings; I-32 generalized as MS-4 |
| I-1, I-2, I-16 (one current revision per record; readiness of the current revision) | amended: a record is a revision, readiness is per revision, no current pointer (§6.4) |
| I-7 (recordings only through `resolve_recording`) | kept for recording sources; content files only through the verified content reader |
| I-10 (summaries recomputed under the DatasetVersion lock) | amended: summaries computed once at composition (§7) |
| I-12, §18.3 (one fingerprint per recording scope; replace) | retired (§6.4) |
| I-17 (DatasetVersion holds membership and recomputed summaries) | amended: immutable composition (§7) |
| I-19 (one `robot_run_id` per recording pipeline run) | kept; snapshot pipelines take one `snapshot_id` and build from its one content |
| §27.2, §28 (`ExternalUnitSource`, `ExternalSourceRevision`, locator-free provenance) | readmitted as `ContentUnitSource` / `ContentSourceRevision`, bound to registered content instead of an `ExternalDatasetRef`; the A2 revision fields (format, version, external revision) move to the SourceSnapshot declaration, out of unit provenance |
| §27.3 (source time precision and clock semantics) | extended with clock domains (§6.2) |
| §18.2 (per-DatasetVersion external unit scope) | not readmitted |
| §13, §20.6 (source fidelity, no universal adapter or data unit) | kept |

## 14. Alternatives considered

- **Keep A4: convert every source to MCAP.** Rejected. It loses source structure (probe §2)
  and would fabricate acquisition timing and an absolute clock for already-synchronous
  LeRobot data.
- **Reference native data in place, without a copy.** Rejected for v1: reproducibility would
  depend on storage SceneOps does not control. Deferred for immutable remote stores that pin
  content themselves (an HF commit, a versioned bucket).
- **One ArtifactRecord per native file.** Rejected. The inventory is already a checksummed
  registry, and per-file records multiply rows by the file count (31,206 readings in mini
  alone) without adding verification.
- **Snapshot identity = content identity (one record per byte set).** Rejected. A new
  upstream revision with identical bytes would collide with the earlier one and either fail
  or overwrite its provenance. One declared revision fetched twice with different bytes
  would produce two unrelated snapshots, and the inconsistency would go undetected.
- **Units bound to the snapshot (declaration) instead of the content.** Rejected.
  Byte-identical upstream revisions would duplicate every unit, revision, payload reference
  and DatasetVersion member for unchanged data. Canonical builds are a function of bytes,
  so a declaration-scoped unit would claim a difference that does not exist.
- **Format version declared in the descriptor.** Rejected. A build would then read
  provenance, and when several snapshots share a content there is no single descriptor to
  read. The format reader derives the version from the bytes.
- **External units scoped per DatasetVersion (ADR-007 §18.2).** Rejected: it keeps identity
  and membership fused.
- **A draft / frozen DatasetVersion.** Rejected (R1). A draft is persistent mutable
  membership with a lifecycle and a lock, and every reader must check whether it is
  finished. Staging inside one Job gives atomicity without either.
- **Treating every clock as absolute ns.** Rejected (R4). Relative LeRobot time would then
  look comparable across episodes and with epoch clocks.
- **One persisted generic "aligned view" for every scope.** Rejected for now: it would erase
  Episode / Scene output semantics. The shared part is the engine and the stream contract.
- **Native column references for numeric streams instead of inline values.** Rejected on
  measurement (OQ-3): the heaviest probed episode is 2.8–3.5 MB inline.
- **The frame rate on the clock domain.** Rejected (OQ-2): it would make one unit's sampling
  rate part of what decides comparability.
- **LeRobot `next.*` as event streams.** Rejected (§6.6): a per-frame signal is not a
  sequence of events. In pusht, `next.done` is true on the last two frames of all 206
  episodes, so a derived "end" event would be wrong.

## 15. Consequences

Positive:

- External sources keep their structure, tokens, timing, bytes and provenance. nuScenes
  radar, keyframes and annotations become reachable, and LeRobot gets an inbound path.
- One unit can belong to many DatasetVersions without copies. A DatasetVersion is a
  reproducible, complete artifact from the moment it exists.
- One alignment kernel, one time unit and explicit clock domains for every scope.

Negative / costs:

- Two builders per domain to keep semantically consistent. A cross-source test must hold
  them to the same schema rules.
- Every Scene and Episode id changes (unit_id v2). The golden contract is regenerated and
  verified against raw inputs (§12), not migrated.
- A second publication / registration path to operate and recover.
- Snapshot storage duplicates the selected source once (5.05 GiB for nuScenes mini with
  the CAN extracts of its own scenes; the 9.4 GB raw tree includes 4.63 GB of CAN data for
  every nuScenes scene).
- Clock domains are new contract surface. Every recording manifest declares its clocks with
  origin `source`; build configs do not change (OQ-9).
- An upstream release with changed bytes creates new units. Correspondence across releases
  with different bytes needs a separate derived relation, which does not exist yet.
  Byte-identical releases share content and units.
- Two source-preservation records (content, snapshot) instead of one, plus a
  content-to-snapshot query for full provenance.
- A descriptor cannot be corrected in place (OQ-10).

## 16. Open questions

Decisions marked M3.0 were taken on 2026-10-22. Alternatives, measurements and tradeoffs
are in the
[M3.0 record](../history/multi-source-native-integration-architecture.md) §3.

```text
OQ-1  DECIDED (M3.0): always copy into SceneOps storage. No in-place reference, field or
      resolver in M3–M5. The declaration's selection keeps storage proportional.
OQ-2  DECIDED (M3.0): t_ns = round_half_even(frame_index · 10⁹ / fps) on lerobot.frame_time,
      origin unit. fps is declared once on the Episode frame timeline, not on the clock
      domain; frame indices are recovered from t_ns and not stored per occurrence. A
      recorded timestamp that is not float32(frame_index / fps) is rejected, not snapped
      (§6.2, §6.6). Rejected: a rational clock (breaks integer ns), float32 timestamp × 10⁹
      (off the grid on 80–96 % of probed frames, by up to 916 ns).
OQ-3  DECIDED (M3.0): inline numeric values; no native column reference type. Revisit only
      when a measured episode manifest exceeds ~64 MiB or ~2 s strict load.
OQ-4  DECIDED (R1): immutable at creation; staged inside COMPOSE_DATASET_VERSION; no draft.
OQ-5  DECIDED (M3.0): nuScenes unit key = scene.token; LeRobot = str(episode_index).
OQ-6  DECIDED (M3.0): native nuScenes labels are imported in M3, as its last slice,
      anchored on the sample (LIDAR_TOP at sample.timestamp, group_ref = sample.token) and
      resolved against the Scene manifest, independently of any sample view (§9).
      render-labels stays the label path for RobotRun Scenes.
OQ-7  OPEN until M5: whether the reference corpus moves its Scene fixtures to native
      nuScenes snapshots while RobotRun Scenes stay covered by the streaming equivalence
      journey. It needs the full mini snapshot (~9.3 GiB of Docker VM space with margins).
OQ-8  OPEN: LeRobot v2.x is unsupported (the builder rejects it). Multi-file and multi-task
      episodes follow the reader rules but stay unprobed until a dataset with them is pinned.
OQ-9  DECIDED (M3.0): every recording clock has origin source, set by the recording
      builders with no configuration field. unix_epoch is used only for content clocks whose
      format defines an epoch the data agrees with (nuscenes.timestamp). A recording
      unix_epoch assertion is added as an explicit build-config field only when a consumer
      needs cross-RobotRun comparison.
OQ-10 DECIDED (M3.0): no correction mechanism; one declaration with another descriptor
      stays a conflict. Reference descriptors carry one annotation, license. A correction,
      if ever needed, is an append-only, separately identified annotation record per
      snapshot, never a descriptor rewrite.
```

The M3.0 record's contract details D1–D14 are accepted as M3 implementation contracts.
D3 (builder-assigned media types), D8 (`component_names`) and D9 (`transition` streams) are
decided as written in §6.5 and §6.6.
