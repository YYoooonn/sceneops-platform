# Ingress boundaries

> How data enters SceneOps and how the two ingress families meet canonical building.
> Decision rationale: [ADR-015](../adr/015-multi-source-platform.md). Acquisition is
> implemented end to end. Dataset Integration preserves and registers external sources
> (`publish-snapshot`, `REGISTER_SOURCE_SNAPSHOT`); LeRobot v3.0 content is canonicalized
> into Episodes ([Episode domain](./episode-domain.md)), and no other format has a
> content builder yet ([limitations](./limitations.md) §5).

## 1. Responsibilities

| Family | Owns | Never does | Contracts |
| --- | --- | --- | --- |
| **Acquisition** | live / recorded robot data capture, the run lifecycle, recording integrity, publication, `REGISTER_ROBOT_RUN`, the RobotRun | decide units, clocks or channel meaning; name a Scene, Episode or DatasetVersion | `sceneops_core.robots` (`RobotRunManifest`, `RobotRunRecord`) |
| **Dataset Integration** | external dataset preservation: native bytes (SourceContent) and upstream declarations (SourceSnapshot) | create a RobotRun; interpret, convert or filter inside a file; decide units, clocks or channel meaning | `sceneops_core.sources` (inventory, declaration, descriptor) |
| **Canonical building** | interpreting one pinned source as Scenes / Episodes: unit boundaries, clock, timestamp normalization, channel meaning, decoding | modify its source; read a snapshot descriptor; name a location | `sceneops_core.provenance` (source bindings, source revisions, pinned builder inputs, producer fingerprint, unit and revision identity); `sceneops_core.temporal` (clock domains, comparability, source time, frame grid) |

Neither ingress family passes through the other, and neither ingress contract imports
the other or the canonical layer. Canonical building reads both through explicit
source bindings; there is no common ingestion abstraction and no generic data unit.
Unit and revision identity derive from the bindings alone (§6). Registration and
DatasetVersion membership are downstream of them and are described in the
[Scene domain](./scene-domain.md), [Episode domain](./episode-domain.md) and
[Data model](./data-model.md).

## 2. Process-to-process schema flow

Acquisition (implemented):

```text
producer -> ros2-kafka-bridge   TelemetryEnvelope + run lifecycle events on Kafka
apps/capture                    finalized MCAP + capture_receipt.json      (durable handoff)
apps/publisher                  recording object, then RobotRunManifest v1 (publication marker)
REGISTER_ROBOT_RUN              params {manifest_uri}
                                -> RobotRunRecord + ROBOT_RUN_RECORDING / ROBOT_RUN_MANIFEST
                                   ArtifactRecords, one transaction
BUILD_RECORDING_SCENES /        params robot_run_id, build_config (no DatasetVersion)
BUILD_RECORDING_EPISODES          resolve_recording -> VerifiedRecording (local, checksum-verified)
                                  -> PinnedRecording                       builder input
                                  -> manifests: lineage RecordingSegmentSource
                                     + ProducerInfo over RecordingSourceRevision
REGISTER_SCENES / REGISTER_EPISODES  params manifest_artifact_ids of one build
                                  -> durability and integrity gate -> unit + revision rows
                                  -> RevisionPins; no DatasetVersion
COMPOSE_DATASET_VERSION         params dataset_id, dataset_version, pins | selector
                                  -> one immutable DatasetVersion + members, one transaction
```

Dataset Integration (§3.1):

```text
local source tree + SnapshotDeclaration
apps/publisher publish-snapshot files under content_id, SourceContentInventory,
                                then SourceSnapshotDescriptor (publication marker)
REGISTER_SOURCE_SNAPSHOT        params {descriptor_uri}
                                  -> SourceContent + SourceSnapshot records and their
                                     SOURCE_CONTENT_INVENTORY / SOURCE_SNAPSHOT_DESCRIPTOR
                                     ArtifactRecords, one transaction
BUILD_SNAPSHOT_SCENES /         params snapshot_id, build_config, unit_keys? (no DatasetVersion)
BUILD_SNAPSHOT_EPISODES           snapshot_id -> content_id once (recorded on the Job)
                                  -> the builder of the content's registered format
                                     (nuscenes -> Scenes, lerobot -> Episodes)
                                  -> PinnedContent + the verified files its reader reads
                                  -> manifests: lineage ContentUnitSource
                                     + ProducerInfo over ContentSourceRevision;
                                     payloads PayloadRef to the preserved files in
                                     place, no payload object
REGISTER_SCENES / REGISTER_EPISODES  as above; the gate checks every PayloadRef
                                  against the content inventory and the store
```

## 3. Dataset Integration output records

`sceneops_core.sources`. Both records are canonical JSON, strict (unknown fields are
rejected) and read only through `load_canonical_*`, which also requires the bytes to be
canonical.

```text
SourceContentInventory   sceneops.source_content_inventory/v1
  files[]                path, size_bytes, sha256; sorted by path, unique, at least one
  content_checksum       sha256(canonical_json(files[]))
content_id               content-<sha256(canonical_json({content_id_schema: "sceneops.content_id/v1",
                                                         content_checksum}))[:32]>

SnapshotDeclaration      format, upstream, upstream_revision, selection {include[]}
  selection_checksum     sha256(canonical_json(selection))
snapshot_id              snapshot-<sha256(canonical_json({snapshot_id_schema: "sceneops.snapshot_id/v1",
                                    format, upstream, upstream_revision,
                                    selection_checksum}))[:32]>

SourceSnapshotDescriptor sceneops.source_snapshot_descriptor/v1
  declaration, content_id, annotations {open identifier: string}
  descriptor_checksum    sha256 of its canonical bytes
```

- `content_id` depends on the file list only; `snapshot_id` on the declaration only.
  Identical bytes under two declarations are one content and two snapshots. One
  declaration with two byte sets, or with two descriptors, is one `snapshot_id` with a
  conflict that registration must refuse.
- Paths are relative to the source root and kept verbatim. A path with more than one
  spelling (absolute, `.` / `..` / empty segments, trailing `/`, `\`, surrounding
  whitespace, control characters) is rejected, never normalized.
- `include` patterns are relative, sorted and unique; publication applies them and the
  inventory records the result.
- The format's own version is not declared; a format reader derives it from the bytes.
  Retrieval facts (local path, URL, time, operator) have no field.

### 3.1 Publication and registration

`publish-snapshot` (`sceneops_sources.publication`, run by `apps/publisher`) is
database-free. It selects the declared files from a local tree (symbolic links are never
followed; every pattern must match a file), hashes them into the inventory, refuses a
declaration already published with another descriptor, and then writes, write-once:
every file under its content id (bytes already stored are reused), the inventory, and
the descriptor last. Without a descriptor nothing is published, and a re-run reuses
every object it finds. Object keys: [Storage layout](./storage-layout.md) §3.

`REGISTER_SOURCE_SNAPSHOT {descriptor_uri}` (`sceneops_worker/sources/registration.py`)
is the only writer of the Source Registry ([Data model](./data-model.md) §5.1). It
reads, verifies and records; it never writes an object:

```text
S1 trusted location   the URI is exactly {configured source root}/snapshots/{snapshot_id}/
                      descriptor.json (ArtifactSettings.source_root_uri); the inventory
                      is read from that root, never from a caller-supplied location
S2 descriptor         present, canonical, and the descriptor of that snapshot id
S3 snapshot known     same content and descriptor -> no-op, nothing else is read
S4 inventory          present, canonical, the descriptor's content; the declared
                      selection selects exactly the inventoried paths, each pattern
                      at least one
S5 content known      same format -> reused: its files were verified at its first
                      registration and are write-once; the inventory has its checksum
   content new        one listing of its files prefix holds exactly the inventory
                      (sizes), then every object is read back and hashed
S6 one transaction    inventory ArtifactRecord + content (if new), descriptor
                      ArtifactRecord + snapshot; commit
```

Every record is inserted only if absent and then decided against the stored row
(`decide_content_registration`, `decide_snapshot_registration`):

| Case | Content | Snapshot | Result |
| --- | --- | --- | --- |
| new declaration of new bytes | inserted | inserted | `created` / `created` |
| new declaration of registered bytes | reused | inserted | `existing` / `created` |
| same declaration, bytes and descriptor | reused | no-op | `existing` / `existing` |
| same declaration, other bytes | — | — | `SnapshotContentConflictError` |
| same declaration, other descriptor (annotations) | — | — | `SnapshotDescriptorConflictError` |
| registered bytes under another format | — | — | `ContentFormatConflictError` |

A conflict, a verification failure or a crash before commit writes no row. Concurrent
registrations are decided by the primary keys, not by locks: the insert of a key
another transaction holds waits for it, and the loser decides again against the
committed row, so identical registrations converge and conflicting ones fail. The Job
result names `snapshot_id` and `content_id` and whether each record was `created` or
`existing`; the Job is the registering execution, recorded as the `job_id` of the
ArtifactRecords it inserted. A crash after the descriptor is recovered by registering
again. The records are read through `GET /source-snapshots[?content_id=]`,
`GET /source-snapshots/{snapshot_id}` and `GET /source-contents/{content_id}`; the Job
is submitted through the Jobs API.

Registration verifies preservation only. A format without a builder registers, and
whether a builder supports the format version in the bytes is decided at build time.

## 4. Source bindings and revisions

`sceneops_core.provenance.sources`. A canonical unit has exactly one source binding; a
binding projects the source revision the producer fingerprint is computed over.

| `source_kind` | Binding (per unit) | Revision (fingerprint input) |
| --- | --- | --- |
| `recording` | `RecordingSegmentSource`: `robot_run_id` (= `source_id`), `recording_artifact_id` (= `art-robotrun-<robot_run_id>`), `recording_checksum`, `source_clock`, `[start, end)`, `unit_key` | `RecordingSourceRevision`: `robot_run_id`, `recording_checksum` |
| `content` | `ContentUnitSource`: `content_id` (= `content_id_for(content_checksum)` = `source_id`), `content_checksum`, `source_clock`, `[start, end)`, `unit_key` (the source's own key, verbatim) | `ContentSourceRevision`: `content_id`, `content_checksum` |

- `SourceRevision` is the closed union of the two revisions, discriminated by
  `source_kind`; `compute_producer_fingerprint` and `ProducerInfo` accept either. The
  `source_kind` is part of the fingerprint, and a recording revision serializes as it
  always has.
- A content binding names no SourceSnapshot and copies no descriptor field, so another
  declaration of the same bytes changes no binding or fingerprint. Which snapshot a
  build read is execution provenance, recorded on that build's Job.
- No binding carries a location, DatasetVersion or execution context.
- `SourceBinding` is the closed union of the two bindings. `source_id` is the stable
  source identity a unit is keyed under: the RobotRun id, or the content id, which is
  a function of the bytes.
- `SceneManifest` and `EpisodeManifest` lineage admit either binding: the recording
  builders produce `RecordingSegmentSource`, the nuScenes Scene builder and the
  LeRobot Episode builder `ContentUnitSource`. A manifest variant lands together with
  the builder that produces it, so registration and downstream consumers never
  receive a source kind nothing produces.
- Every payload reference is a `PayloadRef`
  (`sceneops_core.artifacts.schemas.payload`;
  [ADR-017](../adr/017-unified-payload-ref-and-versioned-manifests.md)): a Logical
  URI, the whole object's checksum and size, a media type and an optional selector,
  the same for every source. A recording builder names its RobotRun's observation
  payloads (`observation-payloads/{robot_run_id}/{artifact_id}`), a content builder
  the preserved files of its content in place (`sources/contents/{content_id}/files/
  {path}`, with a builder-asserted media type and, for a video frame, a `video_frame`
  selector in the file's own time base). Registration proves each URI names bytes of
  the unit's own registered source (`sceneops_worker.payload_registration`).
  Downstream of canonical manifests nothing dispatches on the source: consumers read
  a reference through the verified reader, and one that cannot decode a selector
  refuses it explicitly (§8).

### 4.1 Clock domains

`sceneops_core.temporal` ([ADR-015](../adr/015-multi-source-platform.md) §6.2).
Every canonical timestamp is an integer ns count in one clock domain the manifest
declares. Scene and Episode manifests carry `clock_domains[]`, and every timestamped
structure names its clock by id: the binding's `source_clock` (the window), a Scene
channel, pose or group, an Episode stream.

`sceneops_core.temporal` owns what a timestamp means: the source-time value
(`source_time`), clock domains, origins, origin instances and the comparability rule
(`clocks`), and the frame grid (`frame_time`). It knows no source kind and imports
nothing of provenance, the Source Registry, RobotRuns, Scenes or Episodes, so
`PayloadRef`, whose video-frame selector carries a timestamp, depends on time semantics
without loading source provenance.

Origin authority belongs to the unit's source binding (`sceneops_core.provenance.sources`),
which states it in temporal terms: `permitted_clock_origins` says which origins its
clocks may declare, and `origin_scope()` returns the `OriginScope` (a `source` key and a
`unit` key) its clocks' origin instances resolve to. Which clocks a recording declares,
and with which origin, is likewise the recording binding's (`recording_clock_domains`).
Provenance depends on temporal, never the reverse.

The declared window is not a temporal value: it is the binding's `source_clock` and
`[start_timestamp_ns, end_timestamp_ns)` in `lineage.source`. Consumers read it through
the manifest's `declared_window()`, never from the binding (§8).

```text
ClockDomain
  clock          open identifier (validate_source_clock); "<format>.<clock>" for a
                 clock an external format defines
  origin         unix_epoch   absolute; comparable wherever the clock id is used
                 source       absolute within one source (one RobotRun, one content);
                              the epoch is unknown or not asserted
                 unit         relative; zero is the unit's own origin
  resolution_ns  the source's native resolution; every timestamp on the clock is a
                 multiple of it
```

Rules every manifest enforces (`check_clock_domains`):

- `clock_domains` is sorted by clock and declares each clock once. Every clock the
  window or a structure names is declared, and every declared clock is named; an
  undeclared clock and an unused declaration are both rejected.
- Every clock has an origin the binding permits. A recording binding permits only
  `source`, and its builders declare every clock with origin `source` and resolution
  1 ns (`recording_clock_domains`); no build configuration can change it. Capture's
  log time is a host clock and a header stamp's epoch belongs to the robot, and
  neither can be verified from the recording. The rule is checked whenever a manifest
  is constructed or parsed, so an unregistered manifest cannot assert a comparability
  its source does not grant.
- A content binding permits every origin; which one a content clock has is its
  format builder's decision. LeRobot time is `lerobot.frame_time`, origin `unit`,
  resolution 1 ns: zero is the episode's first frame and the data asserts no absolute
  time. nuScenes time is
  `nuscenes.timestamp`, origin `unix_epoch`, resolution 1 000 ns: the tables count
  microseconds since the Unix epoch, sensor readings, ego poses and samples on one
  clock (every ego pose is at its reading's timestamp); CAN is not canonicalized.
- A frame rate is never part of a clock domain. An Episode sampled on a frame grid
  declares it once, on its `frame_timeline` (`round_half_even(k · 10⁹ / fps)`,
  `sceneops_core.temporal.frame_time`), whose clock must have origin `unit`.
- Normalization changes a timestamp's representation, never its domain. Nothing adds
  an epoch to a relative time, shifts a clock or merges two clocks.

Two timestamps are comparable only on the same clock id and the same origin instance
(`ClockInstance`, `require_comparable`). Any instance matches for `unix_epoch`; for
`source` the instance is the binding's source key (`source_kind`, `source_id`), and for
`unit` its unit key (`source_kind`, `source_id`, `unit_key`). Equal timestamp values
never make two instances comparable. So two Scenes of one RobotRun on
`mcap_log_time` are comparable, while two RobotRuns on the same clock id are not.
`SceneManifest.clock_instance(clock)` / `EpisodeManifest.clock_instance(clock)` return
a manifest's instance.

That accessor is the authority. A derived artifact persists the instance as
`(clock, origin, scope)` and states what its manifest issued; nothing derives a scope from a
RobotRun id, a content id, a format or a URI. A reader proves only the shape
(`restore_clock_instance`). Whether a persisted instance is the unit's own takes the pinned
manifest, and a dedicated verifier makes that comparison, not every read
([ADR-021](../adr/021-cross-artifact-temporal-integrity.md)). A scope is opaque: comparability is
equality of the whole value, and no consumer reads an element.

## 5. Canonical builder inputs

`sceneops_core.provenance.pinned`. A builder receives one pinned source and builds from
it alone; the pinned source issues the revision and bindings of what is built, so no
builder assembles provenance from loose fields.

```text
PinnedRecording   robot_run_id, recording_checksum, recording_clock
                  .source_revision -> RecordingSourceRevision
                  .segment_source(source_clock, start, end, unit_key) -> RecordingSegmentSource
PinnedContent     inventory (SourceContentInventory)
                  .source_revision -> ContentSourceRevision
                  .unit_source(source_clock, start, end, unit_key)    -> ContentUnitSource
```

The bytes are not part of the input: each source has a verified resolver that checks
them against the pinned checksum (`resolve_recording` for recordings). Neither input
names a DatasetVersion, and `PinnedContent` names no SourceSnapshot.

## 6. Unit and revision identity

`sceneops_core.provenance.identity` and `sceneops_core.provenance.revisions`. The two
identities answer different questions and neither embeds a DatasetVersion, snapshot,
location or execution context:

```text
unit_id               <domain>-<sha256(canonical_json({unit_id_schema: "sceneops.unit_id/v2",
                          domain, source_kind, source_id, unit_key}))[:32]>
                      domain scene | episode; source_id from the binding
                      scene_id_for(source) / episode_id_for(source)
revision              (unit_id, manifest_checksum); manifest_checksum = sha256 of the
                      canonical manifest bytes
manifest_artifact_id  <domain>-manifest-<sha256(canonical_json({manifest_artifact_id_schema:
                          "sceneops.unit_manifest_artifact_id/v1", unit_id,
                          manifest_checksum}))[:32]>
```

- A unit is "which logical unit of which source". The source revision checksum, the
  window, the clock, the producer and its build configuration are not inputs, so a
  rebuild of the same unit key is the same unit, and identical content declared by
  several snapshots yields the same units.
- `source_kind` is an input, so a RobotRun id spelled like a content id never names
  the same unit.
- A revision is "which canonical interpretation of that unit". The producer
  fingerprint determines it: one unit and one fingerprint have at most one manifest
  checksum.
- `check_revision_pin` accepts a pin only if its artifact id is the one its
  `(unit_id, manifest_checksum)` derives.

`decide_revision_registration` is the pure registration decision for one candidate,
given every registered revision of its unit. It never deletes or repoints a revision,
and a unit has no current revision:

```text
nothing registered for the unit                       NEW_UNIT
candidate manifest_checksum registered                EXISTING_REVISION (no-op; a retry
                                                      converges here)
no revision under the candidate fingerprint           NEW_REVISION
another checksum under the candidate fingerprint      NonDeterministicProducerError
registered revisions already inconsistent, or a       InconsistentRevisionsError /
revision of another unit                              RevisionPinError
```

The builders use the unit and manifest artifact ids. Persistence applies this
decision: `register_revision` on the Scene and Episode repositories inserts the unit
and revision if absent and decides against the committed revisions of the unit,
with the unique keys deciding concurrent registrations
(`sceneops_db.repositories.revisions`, [Data model](./data-model.md) §3). The
registrars register every revision this way after their durability and integrity
gate and return each revision's complete `RevisionPin`
([Scene domain](./scene-domain.md) §3, [Episode domain](./episode-domain.md) §5).

A recording has no native unit key, so a recording builder's unit key carries its
segmentation: `<policy>-<sha256(canonical_json({recording_segmentation_schema:
"sceneops.recording_segmentation/v1", **boundary document}))[:16]>/<segment>`
(`sceneops_core.provenance.segmentation`, ADR-015 §6.3). The boundary document is
exactly the configuration that places unit boundaries; channel, stream and field
selection are not in it, so they change a revision, not the unit.

## 7. Enforced boundaries

`packages/sceneops-core/tests/test_ingress_boundaries.py`:

- `sceneops_core.sources` imports only `sceneops_core.common`;
- `sceneops_core.robots` imports neither `sources` nor the canonical / composition
  modules (`provenance`, `scenes`, `episodes`, `datasets`);
- `sceneops_core.provenance` imports no Scene, Episode or Dataset module;
- builder inputs carry no DatasetVersion, snapshot, format or location field;
- two snapshots of one content give identical content bindings;
- Scene and Episode manifests admit either binding.

`packages/sceneops-sources/tests/test_boundaries.py`: preservation modules name no
format and import only the standard library, core and storage; native format modules
(`lerobot.py`, `nuscenes.py` and their synthetic test trees) import only `pyarrow`
besides those, never a dataset SDK; the workspace lock contains no `lerobot`.

`packages/sceneops-core/tests/test_temporal_boundaries.py`: `sceneops_core.temporal`
imports only `sceneops_core.common`; importing it, or `PayloadRef`, in a fresh
interpreter initializes no provenance, Source Registry, RobotRun, Scene or Episode
module; provenance neither holds nor re-exports a temporal module, and the recording
clock policy stays with the recording binding. Clock comparability across sources:
`test_clock_comparability.py`.

`packages/sceneops-core/tests/test_manifest_envelope_boundaries.py`: the Scene and
Episode schema packages import only `common`, `temporal`, `PayloadRef`, `provenance`
and run records besides themselves, and loading them initializes no build
configuration. The dependency direction is:

```text
common  <-  temporal  <-  provenance (bindings)  <-  {scenes,episodes}.schemas  <-  build configurations, builders, consumers
```

`test_episode_vocabulary_boundaries.py`: the Episode stream roles and field paths are
defined once, in `sceneops_core.episodes.schemas`, and every build configuration,
builder and consumer takes them from there.

Record contracts: `test_source_content.py`; bindings, revisions and pinned inputs:
`test_provenance_sources.py`; fingerprint: `test_producer_fingerprint.py`; unit and
revision identity and the registration decision: `test_unit_identity.py`. Source
Registry decisions: `test_source_registry.py`; publication:
`packages/sceneops-sources/tests`; registration: `apps/worker/tests/sources` (with
real MinIO and PostgreSQL in `*_integration.py`); the PostgreSQL invariants and races:
`packages/sceneops-db/tests/test_source_registry_repository.py`; the reference
snapshots end to end: `make test-infrastructure SUITE=sources`.

## 8. Canonical processing and source-aware lifecycle

A manifest carries its source binding in `lineage.source`, and two kinds of code use
it differently.

**Canonical semantic processing** (schema validation, alignment, profiling,
detection, analytics, sample views and the other canonical consumers) works on what
the manifest declares: clock domains and origin instances, the declared window,
coordinate frames, calibrations, channels and streams, observations and occurrences,
`PayloadRef`s and their media types and selectors. It never chooses behavior by the
source kind, the source format, the source identity (`robot_run_id`, `content_id`,
`source_id`, `unit_key`), the Logical URI namespace or who owns a payload's bytes.
A consumer that cannot decode a reference refuses it by capability (a selector it
does not support, a media type it cannot read), never by source.

**Source-aware lifecycle code** dispatches by source by design: source preservation
and the native readers, the format builders and the label importers, canonical,
payload and label registration (ownership and namespace of every reference), unit
and revision identity, revision projections into per-kind record columns,
persistence, and selection of members by provenance. Storage resolution interprets a
Logical URI's namespace to locate bytes (`PayloadUriResolver`); that is lookup, not
dispatch.

**Anchor resolution** sits between the two: the one anchor resolver
(`SceneAnchorResolver.resolve`) matches a LabelSet anchor's whole source identity
(`source_kind`, `source_id`) against the Scene's binding, and nothing else.

**Temporal semantics.** `sceneops_core.temporal` owns clocks, origins, origin
instances and comparability. The binding issues what is per source: its
`permitted_clock_origins` and its `OriginScope` (`origin_scope()`), and it carries the
declared window. Only the manifest envelope reads these from the binding:

| Envelope operation | Scene | Episode |
| --- | --- | --- |
| the declared window accessor | `SceneManifest.declared_window` | `EpisodeManifest.declared_window` |
| the origin instance accessor (`OriginScope` of the binding) | `SceneManifest.clock_instance` | `EpisodeManifest.clock_instance` |
| every clock has an origin the binding permits | `SceneManifest._check_clock_domains` | `EpisodeManifest._check_clock_domains` |
| every timestamp on the window's clock lies in the window | `SceneManifest._check_source_window` | `EpisodeManifest._check_source_window` |
| a frame timeline is on the window's clock and spans the window | | `EpisodeManifest._check_frame_timeline` |

Every other consumer reads `manifest.declared_window()` and
`manifest.clock_instance(clock)`. The lineage validators also re-derive the producer
fingerprint from the binding's source revision (lineage consistency). None of the
envelope operations branches on the source kind: what differs per source is stated by
the binding itself.

**Enforcement.** `tools/checks/source_dispatch.py` (part of `make check-boundaries`
and of `make test`) checks every production module under `apps/`, `packages/` and
`integrations/` statically and fails on:

- a *dispatch site*: a comparison reading `source_kind` or `format`, or with a
  source-kind or format literal; a comparison reading a binding's identity
  (`source_id`, `robot_run_id`, `content_id`, `unit_key`); a comparison with a payload
  namespace, or a `startswith` / `endswith` test against a Logical URI literal; an
  `isinstance` / `issubclass` / `match` on a binding, source revision or pinned
  source class; a dict keyed by format literals;
- a *binding-time site*: a read of `source_clock`, `start_timestamp_ns`,
  `end_timestamp_ns` or `contains` on a binding, or any read of `origin_scope` or
  `permitted_clock_origins`. A binding is an expression ending in `.lineage.source`,
  or a local name assigned one (also through an alias or `:=`, and inside a closure)
  or annotated with a binding class. The same fields on a declared window, a channel,
  a stream or an aligned output are not binding reads;
- a *scope-opacity site*: reading the elements of a clock scope by subscript, unpacking,
  spreading, iteration, membership, a `match` sequence pattern, a Polars `.list` read or a
  SQL element read. Comparing, storing, serializing or restoring the whole value is
  allowed. This rule also holds in `UPSTREAM` modules; only `SCOPE_OWNERS` (the temporal
  package and the source bindings, which issue a scope or validate its shape) are out of
  it, and neither reads an element today. It sees the direct forms named in the checker's
  docstring, not arbitrary data flow.

A site is allowed only where its table says so, each entry with its reason:

| Table | Granularity | Admits | Holds |
| --- | --- | --- | --- |
| `UPSTREAM` | module path prefix | every rule | provenance, Source Registry contracts, `sceneops-sources`, the native builders, `sceneops-db`, the snapshot build jobs, the canonical / payload / source registrars, the publisher, the native label importer and its job, `integrations/dataset-acquisition` |
| `LIFECYCLE` | module + function | dispatch sites | `_source_id` and `project_*_revision` of the Scene and Episode records (per-kind source columns) |
| `DOWNSTREAM` | module + function | dispatch sites | the LeRobot export adapter's own target format |
| `IDENTITY` | module + function | whole-identity comparisons only | `SceneAnchorResolver.resolve` |
| `ENVELOPE` | module + function | binding-time sites only | the envelope operations above |

The schema packages themselves are checked: a source-kind branch added to a manifest
validator, even to an envelope operation, fails. A listed site that no longer exists
fails too, so the tables cannot go stale. `tools/checks/tests/test_source_dispatch.py`
holds negative and positive controls on real modules (validators, the profiler,
alignment, `ALIGN_EPISODE`, the anchor resolver, registration, unit identity, storage
resolution) under the repository's tables.

Limits of the check: name tracking is flow-insensitive and local to a function and
its closures. It does not follow attributes (`self._source = ...`), containers,
return values or calls into other functions, and it does not see a source identity
used as a dict key or passed to a helper that branches on it. Those remain review
responsibilities. The import direction of these layers is checked by the focused
tests of §7, not by `tools/checks/import_boundaries.py`, which checks package-level
layering only.

`sceneops_core.episodes.alignment` re-exports the recording clock id
`MCAP_LOG_TIME_CLOCK` from `sceneops_core.robots.clock`. No alignment code uses it
(`ALIGN_EPISODE` takes its clock from `declared_window()` or from an explicit
`TemporalSourceContext`), and it is alignment's only import of a recording module.
The constant belongs to the recording layer (`robots.clock`), and its
callers should import it from there.
