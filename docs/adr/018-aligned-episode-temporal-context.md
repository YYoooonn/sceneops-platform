# ADR-018: Temporal context and versioned identity of AlignedEpisode

## Status

**Accepted (2026-11-01, Canonical Domain Boundaries P5.1).** AlignedEpisode only. The
learning and Scene analytics tables still discard the context; see
[Consequences](#consequences).

## Context

A timestamp means something only on its origin instance (ADR-015 §6.2): two timestamps
are comparable exactly when their `ClockInstance` `(clock, origin, scope)` are equal.
Canonical manifests can issue that instance. AlignedEpisode kept only the clock id, so
telling whether two independently derived Episodes could be compared took their
canonical manifests, and a join on `(clock, timestamp)` silently matched unrelated units.
Two RobotRuns on `mcap_log_time`, or two LeRobot Episodes with identical frame times,
looked alike.

Adding fields changes the artifact's bytes, and two parts of the persistence model would
have turned that into a failure or a silent stale read:

- `alignment_key` had no schema input. The new bytes would land at an existing key, and
  a key is write-once, so the write would conflict.
- ALIGN_EPISODE's execution key had no output contract input. An unchanged request would
  return the Job that succeeded under the old schema, and that Job's result names old
  artifacts.

## Decision

**Persist the triple.** `AlignedEpisode` states `source_clock`, `clock_origin` and
`clock_scope` (an ordered tuple of strings), all required; `clock_instance` reconstructs
the instance. `AlignedEpisodeArtifact` schema `v2` is this shape. No default is invented
for a missing part, and nothing infers one from a source type, clock name or URI.

**At Episode level.** All timestamps of one AlignedEpisode are on one clock, so the
instance is stated once and not per step or signal.

**The scope is opaque.** Its elements are whatever the unit's source binding asserted.
Comparability is equality of the whole instance, as it is for an issued instance;
`RevisionPin`, content and artifact ids are not added to it. Code that read `scope[0]` to
learn the source kind would reintroduce source dispatch into canonical processing, which
the ingress boundaries exclude.

**One producer, one reconstruction.** The engine copies what
`manifest.clock_instance(clock)` issues, after the existing clock checks, and never builds
a scope from binding identifiers. `sceneops_core.temporal.restore_clock_instance` is the
reconstruction contract: a canonical clock id, a known origin, an ordered sequence of
non-empty, unambiguous strings, and an empty scope exactly for `unix_epoch`.

**Structural validation is not provenance.** `restore_clock_instance` does not establish
that a scope belongs to the Episode an artifact names; tying the two together takes that
Episode's canonical manifest.

**Algorithm semantics and artifact schema stay separate versions.**
`ALIGNMENT_SEMANTICS_VERSION` stays `v3`: for identical inputs, the timeline, association,
tolerances, quantization, references and error precedence are unchanged. Only
`ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION` moves.

**The output contract is part of both identities.**

| Identity | Before | Now |
| --- | --- | --- |
| `alignment_key` | config hash, semantics version, clock | those + `artifact_schema_version` |
| ALIGN_EPISODE execution key | normalized request params | those + `aligned_artifact_schema_version`, injected by `_align_episode_transform` |

A new schema therefore writes beside the old objects, which stay as they are, and repeats
converge on one key, one object and one ArtifactRecord. Validation and profile reports use
the same key, and VALIDATE, PROFILE and EXPORT identities already follow the aligned
checksum, so none of them needed a change.

**Readers refuse, they do not degrade.** `load_aligned_episode_artifact` requires the
stored document to state the supported version, before anything else about it is read,
and raises `UnsupportedAlignedArtifactSchemaError` otherwise, including when it states
none. The envelope model refuses another version on every other path. Backward
compatibility with v1 is deliberately not offered: a v1 artifact has no origin or scope,
and supplying them would mean inventing them.

## PipelineRun reuse and the upgrade procedure

A PipelineRun's execution key hashes its request and no output version. After a schema
upgrade an **unforced identical request still returns the run that succeeded under the old
schema**; version-aware Job identity does not solve this, because task Jobs carry no
execution key at all. No pipeline-versioning mechanism was added for one migration.

The procedure after an output-contract upgrade:

1. Request the run again with `force: true`. A forced run creates new task Jobs, and they
   write beside the old objects.
2. Later unforced identical requests return the newest matching run, which is now the
   forced one.
3. Nothing is deleted: old runs, Jobs, artifacts and exports stay as records.

If this is skipped, the old aligned artifact is refused loudly at its next read
(`validate_aligned_episode`, `profile_aligned_episode`, `export_learning_data`,
`curate_episodes`) instead of being used with missing context.

Not every request reaches a read. VALIDATE, PROFILE and EXPORT keep their keys across the
upgrade, so an unforced identical request for a v1 artifact returns the Job that already
succeeded, `valid: true` included, without opening the object. A v1 pin taken from a reused
run and followed that way raises no error. Those records are true of the checksum they pin,
but they do not show that today's readers accept it; use the pins of a forced run
([measured](../history/aligned-episode-deployment-acceptance.md)).

## Rejected alternatives

- **Keep the key and replace the object:** breaks write-once and the pinned checksums of
  existing records.
- **Default the origin from the source kind, or read v1 with an empty scope:** an empty
  scope means `unix_epoch`, which would make relative timestamps globally comparable.
- **Bump `ALIGNMENT_SEMANTICS_VERSION`:** the algorithm did not change, and that version
  would then stop meaning what it says.
- **Per-step or per-signal context:** one clock per AlignedEpisode makes it redundant.
- **A temporal registry or table:** the instance is a value carried by the artifact;
  nothing needs to look it up.

## Consequences

- Timestamps of independently derived AlignedEpisodes can be compared, or refused, from
  the artifacts alone, through `require_comparable`.
- Canonical SceneManifest v5 and EpisodeManifest v4 bytes, unit identity and revision
  identity are unchanged.

Open until the follow-up slices:

- **P5.2A:** the learning tables still keep only `source_clock`. The context stops at
  AlignedEpisode, so a Parquet consumer cannot decide comparability from a learning export.
- **P5.2B:** Scene analytics tables are unchanged and carry only clock ids.
- **P5.3:** no check yet ties an artifact's scope to the Episode's canonical manifest, and
  no static rule yet forbids reading scope elements.
- An incremental learning export reuses its base's shards without reading their aligned
  artifacts, so a base built from v1 artifacts carries forward. Its rows are unchanged by
  this decision; curation of such an export refuses the v1 revisions it pins.
- `ALIGNMENT_SEMANTICS_VERSION` is still not an input of the ALIGN_EPISODE execution key.
  A future semantics bump needs the same treatment as this schema bump.
