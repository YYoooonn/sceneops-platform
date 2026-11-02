# ADR-022: Aligned artifact identity

## Status

**Accepted (2026-11-02, reliability gate R6.1).** Changes where aligned artifacts and their reports are
stored and what their records carry. No canonical manifest, aligned or learning schema, alignment
semantics version or clock semantics changes. Evidence:
[aligned artifact identity acceptance](../history/aligned-artifact-identity-acceptance.md).

## Context

`ALIGN_EPISODE` failed for the second DatasetVersion that aligned a revision with a recipe an earlier
version had used, and the object it had just written stayed behind with no record. The inputs of the
identities disagreed:

| Identity | Inputs | Names the DatasetVersion |
| --- | --- | --- |
| Artifact bytes and checksum | source revision (Episode, manifest artifact id and URI, manifest sha256), recipe, the aligned steps | no |
| ArtifactRecord id | Episode, checksum | no |
| `alignment_key` | config hash, semantics version, artifact schema version, clock | no |
| Artifact URI | the DatasetVersion, Episode, source hash, `alignment_key` | **yes** |
| Report record id | aligned artifact id, report checksum | no |
| Report URI | the DatasetVersion, Episode, source hash, `alignment_key`, report kind, report checksum | **yes** |
| `ALIGN_EPISODE` execution key | the DatasetVersion, source sha256s, recipe, artifact schema version | yes |

Equal bytes therefore meant one record id with two URIs, and `ArtifactRecordStore.register` refuses that,
correctly. `align_episode` takes the manifest, the config, the clock and the Episode id, and no
DatasetVersion, so the DatasetVersion cannot reach the bytes. Measured on real PostgreSQL and MinIO, two
versions produced the same checksum.

The same scope was also the only thing `VALIDATE_ALIGNED_EPISODE` and `PROFILE_ALIGNED_EPISODE` used to decide
whether a request might read an artifact, and `EXPORT_LEARNING_DATA` checked no membership at all.

## Decision

**An aligned artifact belongs to its source revision and its recipe. A DatasetVersion decides whether a
revision may be used, and nothing about the result.**

1. The location has the same inputs as the content. A report follows the artifact it describes:

   ```text
   {aligned_root}/{episode_id}/{source_hash}/{alignment_key}.json
   {aligned_root}/{episode_id}/{source_hash}/{alignment_key}.{report_kind}-{sha256}.json
   ```

   The ArtifactRecord id, `alignment_key` and the artifact's bytes keep their inputs.
2. The records are owned by the Episode and carry no `dataset_id` or `dataset_version`, as the canonical
   manifest's do. The Job and PipelineRun on a record are those of its first registration: provenance, not
   identity.
3. Membership is checked on every use, against what the artifact's own bytes say it was computed from:
   - `ALIGN_EPISODE` refuses an Episode the version does not contain, and a pin of any revision but the one
     it pins. It used to accept any registered revision of the Episode, which would have let one version
     produce and read through its name what only another version pins.
   - `VALIDATE_ALIGNED_EPISODE`, `PROFILE_ALIGNED_EPISODE` and `EXPORT_LEARNING_DATA` require the source revision
     to be the one the named version pins. A validation or profile request naming no DatasetVersion asks
     about the artifact alone, and half a scope is refused.
4. Execution keys keep the DatasetVersion. A Job of version A also asserts that A pins the revision, so it
   must not answer B's request. Two Jobs reach one artifact, which is what the content identity is for.
5. Before anything is written, the handler computes the bytes and the id and looks for the record. A record
   is reused if its kind, checksum and size match and its object holds those bytes, at whatever URI the record
   states. A record that differs is `ArtifactRecordConflictError`, and a record whose object is missing or
   changed is `RegisteredArtifactIntegrityError`; neither writes.
6. `ALIGN_EPISODE` takes its records in Episode id order and returns results in request order. Records stay
   locked until the Job's single commit, and versions sharing Episodes now register the same ones: in
   request order, two Jobs crossed over two shared Episodes and PostgreSQL aborted one
   (`DeadlockDetectedError`, measured).

Learning exports and curations stay owned by a DatasetVersion: their manifests and tables state which one,
so their bytes differ per version and no identity disagrees.

## Alternatives

**DatasetVersion in both the record id and the URI.** Consistent, and it fixes the conflict. It would store
identical bytes once per DatasetVersion, and two versions could not recognise that they consumed the same
artifact except by checksum. Nothing in the result depends on the DatasetVersion to justify that.

**A table of which DatasetVersion used which artifact.** Membership already states which revisions a version
pins, and the artifact states its revision, so the relationship can be derived. No query needs more.

**Bumping the artifact schema or alignment semantics.** The bytes did not change, so neither did. A schema
bump to move an address would make two identical documents look like different contracts.

**A record id that names the layout.** An earlier record has the same content, so the same id is true of it,
and reuse at its own URI needs no id change.

## Consequences

- One object and one record per (revision, recipe), however many versions use it.
- The records of a version's aligned artifacts cannot be listed by `dataset_id`. Reach them from a Job
  result, or through a member's revision. No current consumer lists them that way.
- Deleting a DatasetVersion, if the platform ever grows that, must not delete aligned artifacts with it.
- An `ALIGN_EPISODE` request with a non-member pin now fails. Every caller in the repository pins the member's
  revision.
- PostgreSQL and object storage share no transaction. A crash between the write and the commit leaves an
  object with no record, and the next attempt from any version adopts that object. If no attempt comes, it
  stays: there is no garbage collection. Nothing is deleted automatically, because nothing can tell such an
  object from one a commit is about to register.
- An earlier v2 record under the old `datasets/…/aligned/` location stays valid and is reused where it is.
  Earlier schemas stay unreadable as ADR-018 decided.
