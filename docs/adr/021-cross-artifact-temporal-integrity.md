# ADR-021: Cross-artifact temporal integrity

## Status

**Accepted (2026-11-02, Canonical Domain Boundaries P5.3).** Adds verification and an architecture
rule. No canonical manifest, derived schema, clock semantics or unit identity changes. Closes what
[ADR-018](./018-aligned-episode-temporal-context.md), [ADR-019](./019-learning-data-schema-v2-and-contract-aware-jobs.md)
and [ADR-020](./020-scene-analytics-schema-v2.md) left open as "P5.3", and the CURATE_EPISODES entry of
ADR-019. Evidence: [temporal lineage verification](../history/temporal-lineage-verification.md).

## Context

After AlignedEpisode v2, Learning Data v2 and Scene analytics v2, every derived representation states
its clock instance, and readers refuse a malformed one. They cannot refuse a wrong one. A scope is an
opaque value that restores cleanly whoever it belonged to: the scope of another RobotRun in an aligned
artifact passes `restore_clock_instance`, and the export and every reader after it, and it claims
comparability with a unit the artifact has nothing to do with. Only the manifest of the revision the
artifact was derived from can refute it.

Before this decision the checks divided like this:

| Class | Example | Refutes a wrong scope |
| --- | --- | --- |
| Structural | `restore_clock_instance`, footer stamps, one instance per (unit, clock) | no |
| Byte integrity | pinned checksums, `resolve_and_verify_*`, `SceneRevisionContent` | no: the wrong scope's bytes are what was pinned |
| Revision and lineage | `check_revision_pin`, `project_*_revision`, `_check_rows_belong_to` | no: they say which unit, not which instance |
| Source ownership | payload registration gate, raw-source verifiers | no: they stop at the manifest |
| Temporal comparability | `require_comparable` | no: it compares two stated values |
| Cross-artifact temporal ownership | none | the gap |

Two adjacent gaps surfaced in the audit. No static rule forbade interpreting a scope's elements, so a
reader could grow a dependence on what they name. And CURATE_EPISODES kept a key that did not see the
schema of the export it reads.

## Decision

**The pinned canonical manifest is the authority.**

```python
expected = manifest.clock_instance(clock)
actual = restore_clock_instance(source_clock, clock_origin, clock_scope)
```

A derived temporal declaration is valid only when `actual == expected`, with `manifest` the revision that
derived representation pins and `clock` the one it declares. Equality is the whole value. The check reads
no scope element and builds no expected scope from a RobotRun id, a content id, a source kind, a URI or a
format, so it has no source-specific case and needs none. `ClockInstance`, `require_comparable` and
`OriginScope` are unchanged.

**A dedicated verifier makes the comparison; normal reads do not.** Readers keep proving structure and
schema. Resolving a manifest on every sample or row would add a database and an object-store dependency
to every consumer, for a check whose answer cannot change once a revision is pinned.
`tools/reference/temporal_lineage.py` (`make reference-verify-temporal`) resolves what an artifact pins
through read-only API calls and object reads:

```text
AlignedEpisode Artifact v2 -> source_revision -> EpisodeManifest v4 -> clock_instance()
learning_episodes row -> AlignedArtifactRevision -> AlignedEpisode -> EpisodeManifest
scenes / observations / keyframes row -> DatasetVersion member -> SceneManifest v5
```

It reuses what exists instead of restating it: `load_aligned_episode_artifact`,
`AlignedEpisodeValidator`, `SceneOpsDataset` (whose row-ownership checks hold while steps are read),
`check_learning_export_tables`, `read_scene_table_bytes`, `SceneRevisionContent` and
`project_scene_revision`. A row's source provenance is compared as a whole with the pinned revision's
projection, so the verifier holds no rule about which id goes with which source kind. Beyond the clock,
an aligned artifact's steps are compared with what its recipe recomputes from the manifest, since the
engine is pure.

**It is deliberately not independent.** The raw-source verifiers import no SceneOps package, so they can
catch the platform's manifests being wrong. This one needs the platform's manifest API, because that is
the contract under test. Raw source -> manifest is theirs and manifest -> derived is this one's, and neither
replaces the other.

**One run, one resolution per revision.** A manifest cache keyed by the immutable pin lives for a run, and
no persistent cache or service exists. A run reads one manifest per distinct revision and recomputes one
alignment per distinct aligned artifact, however many rows state them. Row checks are in memory, so the
cost is linear in rows.

**Negative controls are self-consistent forgeries.** A control forges its fault in a read-only overlay,
recomputing every checksum, record and pin above it, so the forged bytes are the pinned ones and readers
accept them. A wrong-scope control therefore reaches the manifest comparison and cannot pass by stopping
at an outdated checksum. Nothing is written to the store under verification, and a report is a function of
its inputs alone.

**Scope opacity is an architecture rule.** `tools/checks/source_dispatch.py` rejects direct reads of a
scope's elements in production code: subscripts, unpacking, spreading, iteration, membership, `match`
sequence patterns, Polars `.list` element reads and SQL element reads, through plain-name aliases and
whole-value conversions. Comparing, storing, serializing, logging and restoring the whole value stay
allowed. It holds in source-aware modules too, since a registrar has the binding and no use for a scope's
elements. `SCOPE_OWNERS` (the temporal package and the source bindings) may read elements to issue or
validate a scope; today neither does and no production module reads one. The rule is a guard against direct
forms. It cannot see a scope behind a computed key, a return value, an attribute of another name, a
container, `getattr` or `len`, and the comparison above does not depend on it.

**Multi-source joins state the whole instance.** `query_scene_analytics` validates tables and runs what it
is given; SQL compares what it names. The safe form is in
[Storage layout](../architecture/storage-layout.md). Arbitrary SQL and direct Parquet reads are not
prohibited and cannot be, so they are documented as outside SceneOps reader enforcement. No production
module calls `query_parquet`, and a test keeps it that way.

**CURATE_EPISODES sees what it reads.** Its handler reads the pinned export under today's schema and
decides under today's curation semantics, and its key saw neither. Measured on real PostgreSQL before the
change: a request over a v1 export returned the succeeded Job, whose result names a selection over an
export today's reader refuses. The key now adds `learning_data_schema_version` and
`curation_semantics_version`, which `episode_curation_id` already included. A repeat over a current export
converges on the same curation id, object and record. The aligned artifact schema stays out for the reason
it stays out of EXPORT's key: an export of the current schema names only artifacts of that schema, and a
selection over it stays true of what it pins when a later aligned schema arrives. No other JobType's key
moved, frozen by test.

## Temporal data paths

```text
                    Source binding  (issues OriginScope, permits origins)
                          |
         SceneManifest v5 | EpisodeManifest v4      clock_instance(clock) is the authority
                 |                  |
                 |          align_episode           copies the issued instance
                 |                  |
                 |      AlignedEpisode Artifact v2  (source_clock, clock_origin, clock_scope)
                 |                  |
                 |          EXPORT_LEARNING_DATA    copies the validated instance
                 |                  |
                 |      learning_episodes v2        one row per EpisodeRef; steps and signals
                 |                  |               reach it through SceneOpsDataset
                 |          EpisodeMetadata.clock_instance
                 |
        EXPORT_ANALYTICS_SNAPSHOT                   per row, as the row's own manifest issues it
                 |
   scenes v2 (window) / observations v2 (its channel's clock) / keyframes v2 (the group's clock)

readers    prove shape and schema, on every read
verifier   proves each stated instance against the pinned manifest, when asked
checker    keeps code from reading a scope's elements, at every `make check-boundaries`
```

Two timestamps are comparable exactly when their instances are equal. A Scene and an Episode of one
RobotRun compare on a clock both declare; a clock only one of them declares is not comparable with
anything of the other, and no equality is claimed for it. The reference build configurations are that
case: the Scene is on `sensor.header_stamp` and the Episode on `vehicle.source_time`. No path converts a
clock or aligns across units.

## What the contract does not cover

**`robot_telemetry` and `missions`** are outside it, and this ADR assigns them nothing.

| | |
| --- | --- |
| Time fields | `robot_states.timestamp_us`, `robot_telemetry.timestamp_us` (`message.log_time // 1000`); `missions.started_at` / `ended_at` (the first and last log time, taken for UTC) |
| Acquisition | `RecordingTelemetryReader` over the recording; `INGEST_ROBOT_STATES` and `EXPORT_ROBOT_ANALYTICS_SNAPSHOT` run as direct Jobs, no built-in pipeline has them, and the reference database holds no row |
| Consumers | the API's `/robot-states` and `/missions` reads, the two Parquet tables (no reader in the repository) |
| Establishing the clock | not authoritatively today. The value is the recording's log time, which canonical manifests of a RobotRun declare as `mcap_log_time` with origin `source`, but no row states it and nothing ties a row to that declaration. `missions`' UTC reading is the projection's assumption, not asserted by any source or manifest |
| Risk | a join across RobotRuns, or with Scene or Episode timestamps, on `timestamp_us` compares values whose comparability nothing states |
| Future boundary | a schema of their own: state `(clock, origin, scope)` issued by the RobotRun's recording binding, not by a Scene or Episode, with a footer stamp and a key version as ADR-019 did, and either drop the UTC reading of missions or assert it at acquisition |

**Elsewhere:** PipelineRun identity still sees no output contract, so an unforced identical request returns
the old run and `force: true` is the migration (ADR-018, ADR-019); Job-level versioning does not solve it,
because task Jobs carry no key. A pusht Episode built with `next.*` is not aligned, so a native learning
export needs a build without transitions. The verifier runs when asked, so a wrong scope reaching a normal
read is not refused there. It holds named artifacts and does not discover what was exported.

## Rejected alternatives

- **Resolve the manifest in every reader:** every sample and row gains a database and an object-store
  dependency, for an answer fixed at pinning time.
- **Derive the expected scope from the row's `robot_run_id` or `content_id`:** this would restate
  `OriginScope` issuance in a second place and interpret the scope, the very thing being forbidden.
- **Compare only with the AlignedEpisode at the learning layer:** a wrong aligned artifact and a matching
  row would agree. Each layer is compared with the manifest too.
- **Persist verification results as a new artifact type:** a report is reproducible from what it read, and
  a stored one would be one more derived record to trust.
- **Ban every read of a scope, iteration by the owners included:** structural validation legitimately
  looks at a scope, hence a narrow owner list.
- **A global execution-version mechanism for CURATE's sake:** one transform is enough, as for ADR-019.

## Consequences

- A structurally valid but wrong-unit scope is detected, for every derived family, with a reason naming
  what was stated and what was issued.
- No generic processor needs source-specific logic to decide comparability, and the checker keeps it that
  way for direct forms.
- All persisted contracts, identities and bytes are unchanged. A CURATE Job that succeeded under the earlier
  key runs once more on request and converges.
- Nothing runs the verifier automatically on a production path. The suites and `make` target do.
