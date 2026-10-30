# ADR-017: Unified PayloadRef and versioned canonical manifests

## Status

**Accepted (2026-10-29, R3).** This ADR defines the contracts and the readers. The new
schema versions can be loaded, validated and serialized, and legacy manifests are read
under their own contracts. No builder emits a new version yet, and registration still
admits only the legacy versions. The registration check for `PayloadRef` exists
([Registration gate for PayloadRef](#registration-gate-for-payloadref)) but registration
does not call it. Switching the builders and admitting the new versions is a separate
change (R4), constrained by [Required changes before builders emit the new
versions](#required-changes-before-builders-emit-the-new-versions).

**Amended (2026-10-30, R4).** Clean cutover: SceneManifest v5 and EpisodeManifest v4 are
the only versions, every builder emits them, registration verifies every `PayloadRef`,
and the legacy versions, references and readers are removed. Backward compatibility
with legacy manifests and derived reference data is not kept; regenerable state is
reset and rebuilt. See [R4 cutover](#r4-cutover). The sections on legacy reads,
legacy normalization, the not-yet-admitting gate and migration describe the R3 state.

## Context

[ADR-015](./015-multi-source-platform.md) §6.5 references payloads with a closed union
discriminated by `kind`:

- `ArtifactPayloadRef` (a SceneOps payload artifact, by `artifact_id`);
- `SourceFileRef` (a preserved file, by `content_id` and `path`, optionally one video
  frame).

Both pin sha256 and size, but each names its bytes through registered records, so
every consumer that reads bytes depends on the source kind. That is why
`PayloadLocator` is the one dispatch site downstream of the manifests.
[ADR-016](./016-logical-uri-and-verified-payload-access.md) gave both kinds one grammar
(Logical URI), one resolver and one verified reader. It left manifests unchanged.

Two facts constrain any change to manifests:

- **Manifests are immutable revisions.** A revision is `(unit_id, manifest_checksum)`,
  and DatasetVersions pin it by `RevisionPin`. Rewriting stored bytes would invalidate
  every pin, and so would reading them into a different form and persisting that.
- **The producer fingerprint does not cover the schema version.** It hashes the producer
  id, semantics version, normalized build configuration and source revision
  (`sceneops_core.provenance.producer`). If a builder emitted another schema version
  under an unchanged fingerprint, the checksum would change while the fingerprint did
  not.

## Decision

### PayloadRef

`sceneops_core.artifacts.schemas.payload.PayloadRef` is one concrete, frozen model:

```text
PayloadRef
  uri          canonical Logical URI (ADR-016 grammar; parse_logical_uri)
  checksum     sha256:<64 lowercase hex> of the whole stored object
  size_bytes   int >= 0, the whole stored object (strict: no bool, float or string)
  media_type   lowercase type/subtype, no parameters (sceneops_core.artifacts.media_types)
  selector     null (the whole object) | VideoFrameSelector
```

- It carries no `artifact_id`, `content_id`, `source_kind`, `relative_path` or `kind`.
  Unknown fields are rejected. A recording-derived payload and a preserved dataset file
  are the same kind of value.
- It satisfies `PinnedPayload` (`uri`, `checksum`, `size_bytes`), so
  `VerifiedPayloadReader.read` accepts it unchanged. It is hashable.
- Its checksum and size always pin the whole stored object, also when a selector picks
  one part of it. A verified read therefore checks the whole file, and selecting the
  part is the consumer's job.
- The schema does not decide which bytes a unit may reference (its own recording's
  payloads, its own content's files). That is a registration fact
  ([Validation boundaries](#validation-boundaries)).

### Selectors

```text
VideoFrameSelector   {kind: "video_frame", timestamp_ns}
```

- `timestamp_ns` is the frame's presentation time in the video file's own time base,
  as in the legacy `SourceFrame`. It names no clock domain and is never compared with a
  canonical timestamp. The selector rejects a `clock` field and any other extra field.
- A selector is valid only for a media type it can select from. `video_frame` requires
  top-level type `video`. Any other combination, and any unknown `kind`, is rejected.
- Each selector carries its own `kind`, so another selector can be added without
  re-reading existing bytes.

### Manifest schema versions

| Domain | New version | Legacy version (still read) | Constant |
| --- | --- | --- | --- |
| Scene | `sceneops.scene_manifest/v5` | `sceneops.scene_manifest/v4` | `SCENE_MANIFEST_SCHEMA_V5` / `_V4` |
| Episode | `sceneops.episode_manifest/v4` | `sceneops.episode_manifest/v3` | `EPISODE_MANIFEST_SCHEMA_V4` / `_V3` |

- The new versions differ from the legacy ones only in observation (Scene) and
  occurrence (Episode) `payload`, which is a `PayloadRef`. Clock domains, windows, frame
  timelines, coordinate frames, calibrations, poses, groups, `native_ref`s, values,
  lineage and every ordering and reference rule are unchanged. No new `SceneData` or
  `EpisodeData` type is introduced.
- `SceneManifest` and `EpisodeManifest` remain one runtime type each. `schema_version`
  selects the payload contract through a per-version table (`_PAYLOAD_REF_TYPES`):

  ```text
  scene v4 / episode v3   ArtifactPayloadRef | SourceFileRef   (LegacyPayloadRef)
  scene v5 / episode v4   PayloadRef
  ```

  A manifest that holds a reference outside its version's set fails validation, and so
  does one that mixes kinds. The payload field's type is `ManifestPayloadRef`, a tagged
  union whose discriminator is the legacy `kind` (`artifact`, `source_file`). A value
  without `kind` is a `PayloadRef`.
- The `schema_version` default is the version builders emit. It is still v4 / v3.

### Legacy reads

*Superseded by the [R4 cutover](#r4-cutover): legacy versions are no longer read.*

- `load_canonical_scene_manifest` and `load_canonical_episode_manifest` stay the single
  loaders. They accept exactly `SCENE_MANIFEST_SCHEMA_VERSIONS` /
  `EPISODE_MANIFEST_SCHEMA_VERSIONS`, validate the bytes under that version's contract,
  and still require `canonical(parse(bytes)) == bytes`. Any other `schema_version`
  (including older, future, missing or non-string values) raises
  `Unsupported*ManifestVersionError`.
- A legacy manifest is never normalized into the new version on read. Its model
  re-serializes to the original bytes, so checksums, manifest artifact ids, revision
  rows and `RevisionPin`s are unchanged. No stored manifest or payload object is
  rewritten, and no object key changes.
- `packages/sceneops-core/tests/fixtures/legacy_manifests/` freezes v4 / v3 bytes that
  the production builders wrote: recording Scene and Episode, nuScenes Scene, and
  LeRobot Episode with frame selectors. The tests assert the bytes and the SHA-256
  checksums are unchanged.

### Legacy normalization

*Superseded by the [R4 cutover](#r4-cutover): the mapping and its callers are removed.*

`ArtifactPayloadRef.to_payload_ref(uri=…)` and `SourceFileRef.to_payload_ref(uri=…)` map
one legacy reference to the `PayloadRef` of the same bytes.

- They are pure. The Logical URI is an input, never derived. Production gets it from
  `PayloadLocator.pins`, which resolves it from the ArtifactRecord or the content
  inventory under the ADR-016 mapping rules. So record lookups stay at the
  infrastructure boundary, and core schema code reads no database.
- They keep the checksum (`checksum` or `sha256`), the size and the media type. A
  `frame` becomes a `VideoFrameSelector` with the same `timestamp_ns`.
- They refuse a URI that cannot name the reference. For an artifact reference that is
  any namespace other than `observation-payloads`, or a key whose last segment is not
  the `artifact_id`. For a file reference it is any namespace other than `sources`, or
  a key not naming that content's `path`. A frame on a non-video media type cannot be
  mapped.
- No production consumer calls them yet.

### Canonical data and the artifact envelope

**Canonical processing operates on semantic data and payload capabilities, not on source
identity or provenance.** A manifest's content falls into four layers:

| Layer | Where it is | Who reads it |
| --- | --- | --- |
| 1. Canonical semantic data | `clock_domains`, `frame_timeline`, `coordinate_frames`, `channels` / `streams`, `calibrations`, observations / occurrences (time, values, `native_ref`), `poses`, `groups` | every canonical consumer |
| 2. Identity and revision metadata | not stored: `unit_id` = f(`lineage.source`), revision = manifest checksum, `manifest_artifact_id`, `RevisionPin`; `schema_version` in the bytes | registration, composition, pins |
| 3. Source and build provenance | `lineage.source` (binding), `lineage.producer` (fingerprint) | builders, registration, anchor resolution |
| 4. Payload references | `payload`: in v5 / v4, a `PayloadRef` (Logical URI, integrity, media type, selector) | readers, through the verified reader |

The new versions move layer 4 off source identity. Layers 1–3 keep their place for now.
Some layer-1 semantics still live inside `lineage.source` (layer 3), and this ADR does
not move them. The minimal boundary is the existing read helpers. Consumers read them
from the manifest, never from the binding:

- **Declared temporal window.** `(source_clock, start_timestamp_ns, end_timestamp_ns)`
  of the binding, via `declared_window()`. Revision projections, the Episode profiler,
  alignment and `ALIGN_EPISODE` use the helper.
- **Clock-comparability scope.** `clock_instance(clock)` derives the origin instance
  from `(source_kind, source_id[, unit_key])` of the binding. So whether two units'
  timestamps can be compared depends on source identity.
- **Schema invariants on the window.** "Every timestamp on the window's clock lies in
  the window" and "the frame timeline equals the window" read the binding.

Remaining coupling, for a later refactoring outside R3/R4:

- The window and the comparability scope should become semantic fields (a declared
  window, and an explicit comparability scope or origin-instance token). Then the
  binding would carry identity and provenance only. That is a further schema version
  of its own, with its own semantics-version bumps.
- Label-anchor resolution (`SceneAnchorResolver`) matches an anchor's source identity
  against the Scene's binding. That is identity matching by design (ADR-015 §9), not
  coupling to remove.
- In v4 / v3, payload references themselves name the source (`content_id`), and the
  schema's own-content rule branches on the source kind. Those legacy contracts stay
  as they are.

### Validation boundaries

- **Canonical semantic validation** is the schema. It checks structure, ordering,
  references between channels, frames, calibrations, poses and groups, clock
  declarations, timestamps against the window, frame timelines, `PayloadRef` syntax,
  and selector / media-type validity. For v5 / v4 it never branches on the source kind.
  A Scene whose `PayloadRef` names a file in the `sources` namespace is schema-valid
  even if the Scene is recording-sourced.
- **Registration and provenance validation** stays in the registrars. It checks
  recording ownership, inventory membership, own-content rules, presence of the stored
  objects, and producer-fingerprint and revision rules. For v4 / v3 it is unchanged,
  including the schema's own-content rule (`_check_source_files`).
- **The gate admits no new version yet.** The REGISTER_SCENES / REGISTER_EPISODES gate
  checks only legacy references: it selects `ArtifactPayloadRef` / `SourceFileRef` by
  type. A `PayloadRef` would therefore pass it unchecked. So each registrar has
  `REGISTRABLE_SCHEMA_VERSIONS = {v4}` / `{v3}` and rejects any other version before
  resolving payloads (`*ManifestRejectedError`). Observable behavior is the same as
  before this ADR: new-version bytes are rejected. The equivalent check of `PayloadRef`
  exists but is not called yet (next section).
- **Downstream consumers refuse explicitly.** `PayloadLocator.pins` refuses `PayloadRef`
  (`UnsupportedPayloadRefError`) instead of dropping it. Alignment
  (`require_alignable`) refuses every payload other than `ArtifactPayloadRef`. The
  analytics observation table already does. Because nothing registers a new-version
  manifest, none of them meets one in production.

### Registration gate for PayloadRef

`sceneops_worker.payload_registration` proves for a `PayloadRef` what the legacy gate
proves for `ArtifactPayloadRef` and `SourceFileRef`, against the unit's own registered
source. There is one verifier per source. The registrar already branches on the binding,
so it calls the verifier for its unit's source with every reference of the input in one
batch. Neither verifier branches on the source kind.

```text
verify_recording_payload_refs(context, run: RobotRunRecord, refs)
verify_content_payload_refs(context, content: RegisteredContent, refs)
  -> {Logical URI: physical URI}, or PayloadRegistrationError
```

- **Every reference.** It is re-validated as a `PayloadRef` (canonical URI, checksum,
  size, media type, selector), so a value built without the schema does not pass
  unchecked. Its namespace must be the one where the source's bytes live
  (`observation-payloads` for a RobotRun, `sources` for a content). It must resolve
  under the configured root (`PayloadUriResolver`). A URI referenced more than once must
  carry the same checksum, size and media type each time.
- **Recording.** The key must be the payload key `{robot_run_id}/{artifact_id}`
  (`observation_payload_key`) of the unit's own RobotRun, and no other key shape. The
  ArtifactRecord is found by that artifact id, never by physical URI: `artifacts.uri` is
  not unique, so a URI lookup could return a record of another run. The record must be an
  `OBSERVATION_PAYLOAD` owned by that RobotRun (`owner_type`, `owner_id`), store its bytes
  exactly where the URI resolves, and record exactly the pinned checksum, size and media
  type (the legacy `payload_mismatch` check, applied to the same values). As with
  `ArtifactPayloadRef`, a recording payload is never empty.
- **Content.** The key must lie under the files prefix of the unit's own content, derived
  from its inventory ArtifactRecord (D4) and mapped to a Logical URI. The inventory must
  be stored under the sources root and name that content. The rest of the key is the
  verbatim source path. It must be an inventory entry with exactly the pinned sha256 and
  size (the legacy `source_file_ref_mismatch` check), and the URI must resolve to exactly
  `content_file_uri` of that path. A file of another content is refused even when its
  bytes are identical. The inventory, the descriptor and objects with no inventory entry
  are not content files. The inventory records no media type, so the media type and a
  selector stay the builder's assertion, covered by the manifest checksum. A
  `VideoFrameSelector` is kept as a stored reference. It is not decoded.
- **Every object.** It must be present in the store with the pinned size. That is checked
  by one listing of the RobotRun's payload prefix or the content's files prefix. A stored
  object is never enough to establish ownership. A recording payload needs its
  registration record, and a content file needs its inventory entry.
- **Cost.** Each input gets one record lookup of the distinct artifact ids (recording) and
  one listing. The caller has already loaded the content's inventory once. Repeated
  references are checked once.
- **Registration time and read time.** The gate checks presence and size, not bytes, as
  the legacy gate does. Payloads are hashed when they are published, content files when
  the content is registered, and both are write-once. `VerifiedPayloadReader` re-verifies
  the full-object sha256 on every read. Neither check replaces the other.
- **Read-only.** The gate writes no unit, revision, DatasetVersion member, ArtifactRecord
  or object.

The registrars call it since the [R4 cutover](#r4-cutover); until then
`REGISTRABLE_SCHEMA_VERSIONS` rejected SceneManifest v5 and EpisodeManifest v4.

### Producer fingerprints and revision safety

Re-serializing a registered legacy manifest as the new version keeps its producer
fingerprint and changes its checksum. `decide_revision_registration` rejects that
candidate with `NonDeterministicProducerError`. It never takes it for the registered
revision, and never adds it silently. With the semantics version bumped, the
fingerprint changes and the candidate is a `NEW_REVISION` of the same unit; the legacy
revision and every pin to it stay. `test_manifest_schema_versions.py` asserts both
cases for all four builders' fixtures. No existing fingerprint or registered revision
changes.

## Required changes before builders emit the new versions

These are R4 and must land together, in one change:

1. **Semantics versions.** Every builder that switches must bump its constant:

   | Constant | Module | Now | R4 |
   | --- | --- | --- | --- |
   | `RECORDING_SCENE_SEMANTICS_VERSION` | `sceneops_scenes/recording_builder.py` | 5 | 6 |
   | `NUSCENES_SCENE_SEMANTICS_VERSION` | `sceneops_scenes/nuscenes_builder.py` | 1 | 2 |
   | `RECORDING_EPISODE_SEMANTICS_VERSION` | `sceneops_episodes/recording_builder.py` | 4 | 5 |
   | `LEROBOT_EPISODE_SEMANTICS_VERSION` | `sceneops_episodes/lerobot_builder.py` | 1 | 2 |

   Switch the `schema_version` defaults (or have each builder set the version
   explicitly) in the same change. A builder that switches without its bump fails
   registration loudly as a non-deterministic producer against any unit it built before.
2. **Registration gate.** Add the new versions to `REGISTRABLE_SCHEMA_VERSIONS` only
   together with an equivalent check of each `PayloadRef`, so that no current guarantee
   is lost:
   - a recording unit's URIs name `observation-payloads` keys of its own RobotRun whose
     `OBSERVATION_PAYLOAD` records carry exactly the pinned checksum, size and media type;
   - a content unit's URIs name `sources` keys of files in its own content's inventory
     with exactly the pinned sha256 and size;
   - every object is present with its size.

   The own-content rule moves from the v4 / v3 schema into this gate for the new
   versions. The check is the [Registration gate for
   PayloadRef](#registration-gate-for-payloadref). Admitting the new versions means calling
   it from `_verify_recording_source` / `_verify_content_source` with the input's
   `PayloadRef`s (Scene observations; Episode occurrences that have a payload).
3. **Consumers.** Each of the following must accept `PayloadRef` before it can be
   handed a new-version manifest:
   - `PayloadLocator.pins`: a `PayloadRef` is already a pin;
   - the analytics observation table: its column is `payload_artifact_id`;
   - alignment reference metadata: `artifact_id`;
   - Detection sample assembly;
   - `semantic_scene_content` / `semantic_episode_content`, used by the baseline and
     streaming-equivalence tools, which remove `artifact_id`;
   - the independent reference verifiers in `tools/reference/verification/`, which have
     their own schema constants and payload-shape checks.
4. **Reference expectations.** Manifest checksums in the reference expectations change
   with the new bytes. They are regenerated from the new logic and verified against raw
   inputs ([ADR-015](./015-multi-source-platform.md) R5), never accepted from the
   platform's own output.

## Migration and rollback

*Superseded by the [R4 cutover](#r4-cutover), which resets regenerable state instead of keeping legacy revisions beside new ones.*

- **No migration.** Existing manifests, payload objects, keys, ArtifactRecords, revision
  rows, DatasetVersions and pins are not touched. Legacy versions remain readable
  indefinitely, and nothing copies them into the new version.
- **New revisions sit alongside old ones.** Once R4 lands, rebuilding a unit adds a new
  revision under the new fingerprint, next to the legacy one. Moving DatasetVersions to
  the new revisions is a new composition, never an in-place repoint.
- **Rollback.** Rolling back R4 means the builders emit the legacy versions under their
  previous semantics versions again. Rebuilds then converge on the registered legacy
  revisions (`EXISTING_REVISION`). New-version revisions registered in the meantime stay
  readable only while the reader keeps their version. So removing a version from the
  loaders is allowed only once no registered revision uses it. Rolling back this ADR
  alone (R3) changes no stored state.

## R4 cutover

**Decided 2026-10-30.** No persistent state needs the legacy versions: the local
reference environment is reset and rebuilt from its source data (raw recordings,
preserved SourceContents), and its canonical manifests, revisions, DatasetVersions and
derived outputs are regenerated. So the cutover keeps one production contract instead
of parallel legacy and new paths.

- **One version per domain.** `SceneManifest.schema_version` is the literal
  `sceneops.scene_manifest/v5` (`SCENE_MANIFEST_SCHEMA_V5`) and
  `EpisodeManifest.schema_version` `sceneops.episode_manifest/v4`
  (`EPISODE_MANIFEST_SCHEMA_V4`); the loaders refuse every other version, v4 / v3
  included. The payload field is `PayloadRef`; there is no per-version table and no
  schema rule that branches on the source kind.
- **Removed.** `ArtifactPayloadRef`, `SourceFileRef`, `SourceFrame`,
  `LegacyPayloadRef`, `ManifestPayloadRef`, `to_payload_ref`, the legacy registration
  checks (`sceneops_worker.recordings.payload_refs`, `verify_source_file_refs`),
  `REGISTRABLE_SCHEMA_VERSIONS`, `PayloadLocator` and `UnsupportedPayloadRefError`,
  `UnsupportedSceneContentError`, the frozen legacy fixtures and the test-only
  conversion helpers.
- **Builders.** The semantics versions of the
  [table below](#required-changes-before-builders-emit-the-new-versions) moved to 6, 2,
  5 and 2. A recording builder names each payload by the Logical URI of its published
  key (`observation_payload_uri`); the native builders name each preserved file in
  place by `content_file_payload_ref` (`sceneops_sources.layout`), whose key
  `contents/{content_id}/files/{path}` is the layout publication writes. LeRobot frames
  keep their `video_frame` selectors.
- **Registration.** REGISTER_SCENES / REGISTER_EPISODES call
  `verify_recording_payload_refs` or `verify_content_payload_refs` with every
  `PayloadRef` of the input, after the source binding check and before the
  transaction; a refusal is a `*ManifestRejectedError` and writes nothing.
- **Consumers.** Detection hands the Scene's `PayloadRef` to the backend as its pin
  and refuses a selector (`require_whole_payloads`). The analytics observation table
  carries `payload_uri`, the integrity fields and `payload_selector` (canonical JSON)
  for every source and refuses nothing by source. Alignment carries a reference value
  whole (`reference_uri`, and checksum, size, media type and selector in
  `reference_metadata`; `ALIGNMENT_SEMANTICS_VERSION` `v3`), and the learning export
  keeps the selector (`reference_selector`). `tools/checks/source_dispatch.py` treats a
  comparison with a payload namespace or a Logical URI prefix as source dispatch.
- **Reference verification.** The independent verifiers decode Logical URIs with
  their own implementation of the ADR-016 spelling and hold v5 / v4 bytes to the
  raw-source derivation. Their expectations were re-derived from the raw inputs: the
  recording expectations differ only in the Scene producer's semantics version, the
  mixed-source expectations in the native units' manifest checksums and producer
  fingerprints.
- **Rollback** is a code revert plus another reset of the regenerated state: neither
  code base reads the other's manifests.


- **Separate runtime classes per version** (`SceneManifestV4` / `SceneManifestV5`).
  Rejected: every consumer's type would become a union, while the versions differ in
  one field. The per-version payload table states the difference in one place.
- **Normalize legacy manifests to the new version on read.** Rejected: the model would
  no longer re-serialize to the pinned bytes. Any write-back would create revisions
  nobody built, and conversion needs record lookups that core schema code must not do.
- **Add the new member to the legacy discriminated union** (a third `kind`). Rejected:
  the reference would still carry a source-shaped tag, and a v5 manifest could mix
  kinds.
- **Fold the schema version into the producer fingerprint.** Rejected for now: it
  changes the frozen fingerprint definition and with it every existing fingerprint. A
  per-builder semantics bump expresses the same change without touching registered
  revisions.

## Consequences

- Payload references of the new versions are one source-independent shape for every
  ingress family, and they are read through the ADR-016 reader.
- Two manifest versions per domain are live. Each legacy version is read under its own
  contract until no registered revision uses it.
- Builders, the registration gate and payload consumers still operate on legacy
  references. R4 switches them together with the semantics-version bumps above.
