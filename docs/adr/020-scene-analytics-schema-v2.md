# ADR-020: Scene analytics schema v2

## Status

**Accepted (2026-11-02, Canonical Domain Boundaries P5.2B).** The `scenes`, `observations` and
`keyframes` tables only. Follows [ADR-019](./019-learning-data-schema-v2-and-contract-aware-jobs.md),
whose footer stamp and Job identity it reuses. `robot_telemetry` and `missions` are unchanged. **The
open P5.3 item is closed by [ADR-021](./021-cross-artifact-temporal-integrity.md)**, which also
classifies the robot tables.

## Context

The Scene tables kept clock ids and timestamps. Two RobotRuns on `sensor.header_stamp`, or two
unrelated relative clocks, looked alike, so a join or a concatenation could call their timestamps
simultaneous. ADR-015 defines what makes timestamps comparable: equal `ClockInstance`, which a
manifest issues and these tables did not keep.

The `scenes` table named a Recording Scene's `robot_run_id` and had nothing for a content Scene,
which left its source unnamed. Nothing stated or enforced a table version, and
EXPORT_ANALYTICS_SNAPSHOT's key saw no contract, so an unforced request after an upgrade would
return the Job that wrote the old tables.

## Decision

**Scene analytics schema `v2`, one version for the family.** `SCENE_ANALYTICS_SCHEMA_VERSION` lives in
`sceneops_core.scenes.analytics`, because the execution key needs it and core cannot import
`sceneops_analytics`, which owns the columns and the checks. It is independent of the SceneManifest
version: no canonical manifest or revision changes. The snapshot has no version or id of its own,
because it is three independently content-addressed tables.

**The clock instance at the grain it is declared.**

| Table | Timestamp | Instance columns |
| --- | --- | --- |
| `scenes` | the declared window | `window_clock`, `window_clock_origin`, `window_clock_scope` |
| `observations` | the observation, on its channel's clock | `source_clock`, `clock_origin`, `clock_scope` |
| `keyframes` | the group, on the group's own clock | `source_clock`, `clock_origin`, `clock_scope` |

Origin is `Utf8`, scope `List(Utf8)` with its order preserved. Every value comes from
`manifest.clock_instance(clock)` of the row's own manifest, for the clock that row declares. A
window's clock can be one no observation uses, and a Scene can have several clocks of different
origins, so no row inherits another's instance. Unlike learning steps (ADR-019) the context is on
every row: Scene tables have no single clock to normalize to, and these rows are queried directly
rather than through a dataset object.

**Provenance is not temporal context.** `scenes` adds `source_kind` and `content_id` beside
`robot_run_id`, copied from the revision record. Exactly one of the two ids is set. They name where a
Scene came from; they do not enter a `ClockInstance`, and no analytics code reads them to decide
anything. Comparing timestamps means comparing instances through `restore_clock_instance` and
`require_comparable`, so a scope is never interpreted and never derived from a RobotRun id, a content
id or a URI namespace.

**A record and a manifest are one revision before any row exists.** Builders take
`SceneRevisionContent`, whose construction recomputes the manifest's checksum and holds the record to
the manifest's projection (`project_scene_revision`), so a row cannot state one Scene's id beside
another's clocks. The handler now reads every member's manifest, including for a `scenes`-only
export, because the window's origin is not on the record.

**The stamp is in the footer**, under ADR-019's key `sceneops.table_schema`:
`scene_analytics/v2/<table>`. `AnalyticsTableWriter.write_table` refuses a frame whose columns (in
order) or rows are not what the stamp declares.

**A reader refuses, in this order:** an unreadable file; a footer that does not state exactly this
table of this version (missing, `v1`, unknown, another table, another family); columns that are not
that version's `(name, Arrow type)` list; a missing required value; an invalid instance; two
instances of one clock for one Scene of one DatasetVersion; a `scenes` row whose source is not valid
for its Scene (`SceneRecord` decides this, so analytics compares no source kind) or a second row for
one Scene. `UnsupportedSceneAnalyticsSchemaError` covers the file's contract and
`SceneAnalyticsIntegrityError` its rows.

**Validation is eager, and the supported readers are these:**

| Reader | Validates | Before |
| --- | --- | --- |
| `read_scene_table_bytes`, `AnalyticsTableWriter.read_table` | one whole file: footer, columns, rows | returning the frame |
| `query_scene_analytics` (local files) | every named file, plus agreement between tables on each (Scene, clock) | running the SQL |
| `check_scene_table_files` | the same, with no query | |

`query_parquet` declares no contract, so it checks none and refuses any Scene table, stamped or
recognizably v1 (an unstamped file with exactly the columns of one). Direct Parquet access outside
these readers (a raw `polars.read_parquet`, DuckDB, a downloaded file) is not protected: the stamp is
there to be read, but nothing forces it. SQL also compares only what it is told to: a join must name
all three instance columns.

**The contract is part of Job identity.** EXPORT_ANALYTICS_SNAPSHOT's key adds
`scene_analytics_schema_version`. Every other JobType's key is unchanged, frozen by test.

**Artifact identity did not change, because it already follows the bytes.** A table's key is
`{table}-{sha256}.parquet` and its record id derives from that checksum. The stamp and the new columns
are in those bytes, so a v2 table is always another object and another record, under the same logical
id. A DatasetVersion holds v1 and v2 tables side by side with no write-once conflict. The record's
metadata also names the schema, for inspection; identity does not depend on it.

**Old snapshots stay and stop being read.** v1 objects and records are not rewritten or deleted. Every
supported reader refuses them. No default is invented for their missing context.

## PipelineRun reuse

No pipeline has an EXPORT_ANALYTICS_SNAPSHOT task, enforced by test. It runs only as a direct Job, so
its key alone decides and no reused PipelineRun can return an old snapshot. An unforced request after
the upgrade runs the Job for real; `force: true` runs it again and converges on the same objects.

PipelineRun identity still does not see output contracts (ADR-018, ADR-019), and this ADR does not
change that. A pipeline that gains this task inherits the limitation. A forced PipelineRun does not
refresh a snapshot either, since it has no such task.

## Rejected alternatives

- **Instance only on `scenes`, joined by clock:** a Scene can declare several clocks of different
  origins, and each row's timestamp needs its own.
- **Deriving the window's origin from observations:** the window may be on a clock none uses.
- **A per-Scene clock table:** a new table to join for a value each row states cheaply, and a join
  nothing would force.
- **`query_parquet` validating by view name, or by stamp alone:** names are the caller's, and a v1
  table has no stamp to find. Refusing the shapes outright leaves one door for Scene tables.
- **Trusting columns:** ADR-019's reasoning, unchanged.
- **Adding the version to the object key or to `logical_id`:** the bytes already differ, so it would
  change nothing but the path of unchanged rules.

## Consequences

- A Scene analytics table answers comparability for itself, for any source, through one value.
- A `scenes`-only export reads manifests it did not read before, and every export recomputes each
  manifest's checksum once. Cost scales with total manifest size, inside the existing
  10,000-member bound.
- Canonical SceneManifest v5, EpisodeManifest v4, Scene revisions, DatasetVersion membership and the
  values of every existing column are unchanged.

Open:

- **P5.3:** the checks are structural. Nothing proves that a stated scope belongs to the Scene stating
  it short of reading its manifest, or statically forbids reading scope elements.
- **Robot tables:** `robot_telemetry.timestamp_us` states no clock id at all, only `robot_run_id`. They
  are a separate projection and stay unstamped.
- **PipelineRun identity** does not see output contracts (above).
