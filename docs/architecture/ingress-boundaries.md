# Ingress boundaries

> How data enters SceneOps and how the two ingress families meet canonical building.
> Decision rationale: [ADR-015](../adr/015-multi-source-platform.md). Acquisition is
> implemented end to end. Dataset Integration exists as its output contracts and the
> canonical inputs that read them; no process produces or consumes them yet
> ([limitations](./limitations.md) §5).

## 1. Responsibilities

| Family | Owns | Never does | Contracts |
| --- | --- | --- | --- |
| **Acquisition** | live / recorded robot data capture, the run lifecycle, recording integrity, publication, `REGISTER_ROBOT_RUN`, the RobotRun | decide units, clocks or channel meaning; name a Scene, Episode or DatasetVersion | `sceneops_core.robots` (`RobotRunManifest`, `RobotRunRecord`) |
| **Dataset Integration** | external dataset preservation: native bytes (SourceContent) and upstream declarations (SourceSnapshot) | create a RobotRun; interpret, convert or filter inside a file; decide units, clocks or channel meaning | `sceneops_core.sources` (inventory, declaration, descriptor) |
| **Canonical building** | interpreting one pinned source as Scenes / Episodes: unit boundaries, clock, timestamp normalization, channel meaning, decoding | modify its source; read a snapshot descriptor; name a location | `sceneops_core.provenance` (source bindings, source revisions, pinned builder inputs, producer fingerprint) |

Neither ingress family passes through the other, and neither ingress contract imports
the other or the canonical layer. Canonical building reads both through explicit
source bindings; there is no common ingestion abstraction and no generic data unit.
Canonical identity, manifest revisions and DatasetVersion composition are downstream
of the bindings and are described in the [Scene domain](./scene-domain.md),
[Episode domain](./episode-domain.md) and [Data model](./data-model.md).

## 2. Process-to-process schema flow

Acquisition (implemented):

```text
producer -> ros2-kafka-bridge   TelemetryEnvelope + run lifecycle events on Kafka
apps/capture                    finalized MCAP + capture_receipt.json      (durable handoff)
apps/publisher                  recording object, then RobotRunManifest v1 (publication marker)
REGISTER_ROBOT_RUN              params {manifest_uri}
                                -> RobotRunRecord + ROBOT_RUN_RECORDING / ROBOT_RUN_MANIFEST
                                   ArtifactRecords, one transaction
BUILD_RECORDING_SCENES /        params dataset_id, dataset_version, robot_run_id, build_config
BUILD_RECORDING_EPISODES          resolve_recording -> VerifiedRecording (local, checksum-verified)
                                  -> PinnedRecording                       builder input
                                  -> manifests: lineage RecordingSegmentSource
                                     + ProducerInfo over RecordingSourceRevision
REGISTER_SCENES / REGISTER_EPISODES
```

Dataset Integration (only the schemas named below exist; there is no publication,
registration or snapshot build process yet):

```text
local source tree + SnapshotDeclaration
publication                     files under content_id, SourceContentInventory,
                                then SourceSnapshotDescriptor (publication marker)
registration                    SourceContent + SourceSnapshot records
snapshot build                  snapshot_id -> content_id once (recorded on the Job)
                                  -> PinnedContent                         builder input
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

## 4. Source bindings and revisions

`sceneops_core.provenance.sources`. A canonical unit has exactly one source binding; a
binding projects the source revision the producer fingerprint is computed over.

| `source_kind` | Binding (per unit) | Revision (fingerprint input) |
| --- | --- | --- |
| `recording` | `RecordingSegmentSource`: `robot_run_id`, `recording_artifact_id` (= `art-robotrun-<robot_run_id>`), `recording_checksum`, `source_clock`, `[start, end)`, `unit_key` | `RecordingSourceRevision`: `robot_run_id`, `recording_checksum` |
| `content` | `ContentUnitSource`: `content_id` (= `content_id_for(content_checksum)`), `content_checksum`, `source_clock`, `[start, end)`, `unit_key` (the source's own key, verbatim) | `ContentSourceRevision`: `content_id`, `content_checksum` |

- `SourceRevision` is the closed union of the two revisions, discriminated by
  `source_kind`; `compute_producer_fingerprint` and `ProducerInfo` accept either. The
  `source_kind` is part of the fingerprint, and a recording revision serializes as it
  always has.
- A content binding names no SourceSnapshot and copies no descriptor field, so another
  declaration of the same bytes changes no binding or fingerprint. Which snapshot a
  build read is execution provenance, recorded on that build's Job.
- No binding carries a location, DatasetVersion or execution context.
- `SceneManifest` and `EpisodeManifest` lineage admit `RecordingSegmentSource` only. A
  manifest variant lands together with the builder that produces it, so registration
  and downstream consumers never receive a source kind nothing produces.

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

## 6. Enforced boundaries

`packages/sceneops-core/tests/test_ingress_boundaries.py`:

- `sceneops_core.sources` imports only `sceneops_core.common`;
- `sceneops_core.robots` imports neither `sources` nor the canonical / composition
  modules (`provenance`, `scenes`, `episodes`, `datasets`);
- `sceneops_core.provenance` imports no Scene, Episode or Dataset module;
- builder inputs carry no DatasetVersion, snapshot, format or location field;
- two snapshots of one content give identical content bindings;
- Scene and Episode manifests reject a content binding.

Record contracts: `test_source_content.py`; bindings, revisions and pinned inputs:
`test_provenance_sources.py`; fingerprint: `test_producer_fingerprint.py`.
