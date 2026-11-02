# ADR-019: Learning Data schema v2, enforced versions and contract-aware Job identity

## Status

**Accepted (2026-11-02, Canonical Domain Boundaries P5.2A).** Learning tables only; Scene
analytics tables are unchanged. Amends [ADR-018](./018-aligned-episode-temporal-context.md),
whose context this carries from AlignedEpisode into the learning export. **The open CURATE_EPISODES
and P5.3 items are closed by [ADR-021](./021-cross-artifact-temporal-integrity.md).**

## Context

Two gaps remained after AlignedEpisode v2.

**The context stopped at the aligned artifact.** `learning_episodes` kept `source_clock`
alone, so an exported dataset could not tell whether two Episodes' timestamps are
comparable: two RobotRuns on one clock, or two LeRobot Episodes with identical frame times,
looked alike without reading the aligned artifacts behind them.

**Nothing enforced the learning version, and old successes were still answers.**

- `LEARNING_DATA_SCHEMA_VERSION` was stamped on a manifest and read by nobody: `v999` parsed,
  a manifest stating none was read as the current one, and `SceneOpsDataset.open` and the
  incremental planner never looked.
- An incremental export reuses its base's shards without opening them. Adding a column to
  `learning_episodes` alone leaves steps and signals with the same columns in both versions,
  so old shards would be carried into a new export silently, with no `ShapeError` to catch.
- VALIDATE, PROFILE and EXPORT keys did not see any contract version. Measured in the
  [deployed acceptance of the aligned schema](../history/aligned-episode-deployment-acceptance.md):
  an unforced request returned a Job from before the upgrade, `valid: true` included, for an
  object today's reader refuses.

## Decision

**Learning Data schema `v2`: the triple at the Episode's grain.** `learning_episodes` adds
`clock_origin` (`Utf8`) and `clock_scope` (`List(Utf8)`, order preserved), beside the existing
`source_clock`, copied from the already validated AlignedEpisode v2. Nothing recomputes a scope
from a source binding or infers one from a source type.

**Steps and signals stay normalized.** They state no context. A timestamp's instance is the
one of the `learning_episodes` row owning it through `(episode_id,
aligned_artifact_checksum)`. All timestamps of an AlignedEpisode are on one clock, so per-row
context is redundant, and it was measured at +21% on `learning_steps` and +11.5% on
`to_pylist` per Episode (scale fixture, P5.0: 200 Episodes, 300 steps, 10 channels; polars
1.43.2). Existing step and signal columns and values do not change.

The consequence is a contract: **through `SceneOpsDataset`, every timestamp has exactly one
resolvable instance, or the read is refused.** A shard read on its own carries no temporal
interpretation.

- A row of `learning_episodes` with no valid instance, a second row for one `EpisodeRef`, or
  two instances of one clock for one Episode (a unit's source binding asserts its scope, and
  the Episode's id derives from the same binding) refuses the open. The whole table is
  checked, including rows a curation hides.
- A shard member with no `learning_episodes` row refuses the open.
- A row group holding rows of anyone but the `EpisodeRef` mapped to it, and a single-file row
  owned by no `learning_episodes` row, refuse the read. The shard index says where rows are;
  the rows say whose they are.

**One restoration.** `EpisodeMetadata` exposes `source_clock`, `clock_origin`, `clock_scope` and
`clock_instance` through `restore_clock_instance`, so a consumer compares whole values through
`require_comparable`. A `SequenceSample` leads to its instance through
`get_episode(sample.episode_ref)`. The check is structural, as in ADR-018: it does not establish
that a scope belongs to the Episode stating it.

**Readers refuse, they do not degrade.** v1 is not readable, so no default is invented for its
missing context. Three independent statements have to agree:

| Statement | Checked by |
| --- | --- |
| The manifest's `schemaVersion` | a validator on `LearningDataExportManifest`, so every way of parsing one is gated, with no default. `SceneOpsDataset`, the incremental planner and `check_learning_export_tables` also check the object they hold |
| Each file's footer, `sceneops.table_schema` = `learning_data/<version>/<table>` | `check_learning_table_footer`, when a file is first read |
| The file's columns against that version's `(name, Arrow type)` contract | the same check |

`UnsupportedLearningDataSchemaError` names what was found. It is deliberately not a `ValueError`,
so pydantic does not fold it into a generic validation error.

**The stamp is in the Parquet footer**, written by both production paths: `DataFrame.write_parquet`
for single-file tables and `pq.ParquetWriter` for shards. The stamp names the table too, so one
table cannot stand in for another. No registry and no database table: a file states what it is.
Writers refuse a frame that is not what the stamp declares. Footers are deterministic for a
given Polars and PyArrow; their `created_by` holds the library version, so bytes are only
reproducible under one runtime.

Shards are checked when first read, once per cached footer, before any row is handed out:
`open` keeps its property of doing no per-shard read. A caller about to reuse shards it will not
read asks up front through `check_learning_export_tables`, which fetches each footer with a ranged
read.

**Incremental export refuses an incompatible base before reuse.** `_resolve_base_export_manifest`
parses the manifest and proves every table of it, before the delta is read or anything is
written, and raises `IncrementalExportUnsupportedBaseError` naming what it found. The cost is one
ranged footer read per base shard. A refused request writes no object and no record. Old exports
are not deleted or changed; they coexist unreadable.

**The contract is part of Job identity where the result depends on it.**

| JobType | Injected | Why |
| --- | --- | --- |
| VALIDATE_ALIGNED_EPISODE | `aligned_artifact_schema_version`, `validation_semantics_version` | its result is a statement about the artifact as a reader of that schema and analysis reads it |
| PROFILE_ALIGNED_EPISODE | `aligned_artifact_schema_version`, `profile_semantics_version` | same |
| EXPORT_LEARNING_DATA | `learning_data_schema_version` | its result names an export of that schema |

Every other JobType keeps its key, frozen by test. EXPORT does not inject the aligned artifact
schema: inputs are pinned by content, and a finished export does not need them to stay readable.
It is complete and current as long as its own schema is, so a later aligned schema alone must not
repeat it, and `export_id` does not depend on it either.

**Export identity keeps its existing input.** `export_id` already hashed the schema version, so v2
gets new ids and, because every object key holds `export_id[:16]`, new locations. No competing
identity was added and no shard key can conflict with an old object.

## PipelineRun reuse is not solved here

A PipelineRun's key hashes its request, and task Jobs carry no execution key, so nothing above
reaches it. The upgrade procedure is ADR-018's: an identical unforced request returns the old
run; `force: true` runs the current contract and is what later unforced requests return; old runs,
Jobs and objects stay. A skipped step is not silent, since whatever an old run names is refused at
its next read. `force` is an upgrade procedure, not a replacement for pipeline identity that sees
output contracts, which remains open. All of it was
[measured across real deployment swaps](../history/learning-schema-deployment-acceptance.md).

## Rejected alternatives

- **Origin and scope on every step and signal row:** measured cost above for a value one Episode
  row states.
- **A sidecar schema registry or table:** the version is a property of a file, and a lookup could
  disagree with it.
- **Trusting the manifest alone:** a stale file under a correct manifest would be read, and only
  `learning_episodes` would look different.
- **Trusting columns:** steps and signals have the same columns in v1 and v2.
- **Reading v1 with defaulted context, or rewriting v1 in place:** the first invents context, the
  second breaks write-once and the checksums records pin.
- **Injecting the aligned schema into EXPORT's key:** would turn a valid finished export into a
  refused request on a later aligned bump, for an unchanged `export_id`.
- **A global execution-version mechanism:** three transforms are enough, and PipelineRun identity
  is a separate problem.

## Consequences

- A learning export answers temporal comparability by itself, for any source, through one value.
  Consumers do not branch on source kind, dataset format or URI namespace.
- Existing v1 exports stay in place and are refused by every reader, and curation refuses them.
- Canonical SceneManifest v5 and EpisodeManifest v4, AlignedEpisode v2, alignment semantics
  and all step and signal values are unchanged.

Open:

- **PipelineRun identity** does not see output contracts (above).
- **CURATE_EPISODES** keeps its key. Its result pins an export by checksum, so an unforced repeat
  over a v1 export returns the old Job; the curation it names is refused by `SceneOpsDataset`,
  which checks the export it pins.
- **P5.2B:** Scene analytics tables still carry clock ids only (closed by
  [ADR-020](./020-scene-analytics-schema-v2.md)).
- **P5.3:** nothing ties a stated scope to the Episode's canonical manifest, or statically forbids
  reading scope elements.
