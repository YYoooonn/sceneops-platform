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
| **Canonical building** | interpreting one pinned source as Scenes / Episodes: unit boundaries, clock, timestamp normalization, channel meaning, decoding | modify its source; read a snapshot descriptor; name a location | `sceneops_core.provenance` (source bindings, source revisions, pinned builder inputs, producer fingerprint, unit and revision identity) |

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

Dataset Integration (preservation and registration implemented, §3.1; the snapshot
build exists only as its builder input contracts):

```text
local source tree + SnapshotDeclaration
apps/publisher publish-snapshot files under content_id, SourceContentInventory,
                                then SourceSnapshotDescriptor (publication marker)
REGISTER_SOURCE_SNAPSHOT        params {descriptor_uri}
                                  -> SourceContent + SourceSnapshot records and their
                                     SOURCE_CONTENT_INVENTORY / SOURCE_SNAPSHOT_DESCRIPTOR
                                     ArtifactRecords, one transaction
snapshot build                  snapshot_id -> content_id once (recorded on the Job)
  (not implemented)               -> PinnedContent                         builder input
                                  -> manifests: lineage ContentUnitSource
                                     + ProducerInfo over ContentSourceRevision
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
- `EpisodeManifest` lineage admits either binding: the LeRobot Episode builder
  produces `ContentUnitSource`. `SceneManifest` lineage admits `RecordingSegmentSource`
  only until a content Scene builder exists. A manifest variant lands together with
  the builder that produces it, so registration and downstream consumers never
  receive a source kind nothing produces.
- Payload references are the closed union `PayloadRef`
  (`sceneops_core.artifacts.schemas.payload`): `ArtifactPayloadRef` (a SceneOps-owned
  payload object) or `SourceFileRef` (a preserved file of the unit's own content, by
  `content_id`, `path`, `sha256`, `size_bytes`, a builder-asserted `media_type` and an
  optional `frame {timestamp_ns}` in the file's own time base). Scene observations take
  `ArtifactPayloadRef` only. Downstream of canonical manifests, payload resolution is
  the only place that dispatches on the reference kind; a consumer that cannot resolve
  a kind refuses it explicitly.

### 4.1 Clock domains

`sceneops_core.provenance.clocks` ([ADR-015](../adr/015-multi-source-platform.md) §6.2).
Every canonical timestamp is an integer ns count in one clock domain the manifest
declares. Scene and Episode manifests carry `clock_domains[]`, and every timestamped
structure names its clock by id: the binding's `source_clock` (the window), a Scene
channel, pose or group, an Episode stream.

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
- A recording binding declares every clock with origin `source` and resolution 1 ns.
  The recording builders set this (`recording_clock_domains`); no build configuration
  can change it. Capture's log time is a host clock and a header stamp's epoch belongs
  to the robot, and neither can be verified from the recording.
- A content binding's origins are its format builder's decision. LeRobot time is
  `lerobot.frame_time`, origin `unit`, resolution 1 ns: zero is the episode's first
  frame and the data asserts no absolute time.
- A frame rate is never part of a clock domain. An Episode sampled on a frame grid
  declares it once, on its `frame_timeline` (`round_half_even(k · 10⁹ / fps)`,
  `sceneops_core.provenance.frame_time`), whose clock must have origin `unit`.
- Normalization changes a timestamp's representation, never its domain. Nothing adds
  an epoch to a relative time, shifts a clock or merges two clocks.

Two timestamps are comparable only on the same clock id and the same origin instance
(`ClockInstance`, `require_comparable`). Any instance matches for `unix_epoch`; for
`source` the instance is the source (`source_kind`, `source_id`), and for `unit` it is
the unit (`source_kind`, `source_id`, `unit_key`). So two Scenes of one RobotRun on
`mcap_log_time` are comparable, while two RobotRuns on the same clock id are not.
`SceneManifest.clock_instance(clock)` / `EpisodeManifest.clock_instance(clock)` return
a manifest's instance.

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
- Scene manifests reject a content binding; Episode manifests admit either binding.

`packages/sceneops-sources/tests/test_boundaries.py`: preservation modules name no
format and import only the standard library, core and storage; native format modules
(`lerobot.py`) import only `pyarrow` besides those, never a dataset SDK; the workspace
lock contains no `lerobot`.

Record contracts: `test_source_content.py`; bindings, revisions and pinned inputs:
`test_provenance_sources.py`; fingerprint: `test_producer_fingerprint.py`; unit and
revision identity and the registration decision: `test_unit_identity.py`. Source
Registry decisions: `test_source_registry.py`; publication:
`packages/sceneops-sources/tests`; registration: `apps/worker/tests/sources` (with
real MinIO and PostgreSQL in `*_integration.py`); the PostgreSQL invariants and races:
`packages/sceneops-db/tests/test_source_registry_repository.py`; the reference
snapshots end to end: `make test-infrastructure SUITE=sources`.
