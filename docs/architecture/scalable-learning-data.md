# Scalable Learning Data Architecture

> The production learning-data storage and access architecture as implemented, with
> its frozen contracts. Like [robot-learning-data.md](./robot-learning-data.md) and
> [overview.md](./overview.md), it describes what was checked against the code. The
> measurements and design record behind it is a point-in-time document,
> [learning-data-scaling-baseline.md](../history/learning-data-scaling-baseline.md);
> this document states the conclusions and that one is the evidence.

## 1. Purpose

[Robot learning data layer](./robot-learning-data.md) defines SceneOps's canonical
learning-data representation: `AlignedEpisode` -> columnar Parquet export ->
`SceneOpsDataset` -> `SequenceSampler` -> consumer adapters. This architecture makes
that same logical model scale: bounded physical shards, selective and shard-aware
bulk reads, bounded caches, and immutable incremental exports, without changing the
logical semantics (`EpisodeRef`, ABSENT/MISSING, `FeatureProjection` /
`FeatureSchema`, `SequenceSampler` window semantics).

## 2. Final production flow

```text
Canonical Episode / AlignedEpisode        (unchanged)
        |
        v
Learning Data Export (EXPORT_LEARNING_DATA)
        |
        v
learning_episodes.parquet                 (single file, metadata-scale)
        +
bounded learning_steps / learning_signals shards   (v2-sharded, §4)
        |
        v
LearningDataExportManifest + LearningDataShardIndex   (§3, physical source of truth)
        |
        v
SceneOpsDataset.open()                    (semantic access boundary, §3)
        |
        v
selective single-EpisodeRef reads   <-or->   shard-aware bulk access   (§5)
        |
        v
SequenceSampler -> NumPy / Torch / external adapters   (unchanged)
```

Every arrow above is a real, tested, currently-production code path — not
a target architecture. `EXPORT_LEARNING_DATA` (`apps/worker/
sceneops_worker/jobs/dataset/export_learning_data.py`) is the only
production writer; it always produces the v2-sharded layout (§11).

## 3. Frozen contracts

Five identities recur through this whole architecture. Once opened, a
`SceneOpsDataset` never needs to know how many increments, revisions, or
prior exports produced the manifest it was handed — the manifest and the
shard index are the only physical-layout source of truth it reads.

```text
EpisodeRef
= episode_id + aligned_artifact_checksum
  The read-side identity for one aligned revision.
  Never episode_id alone -- two EpisodeRefs with the same episode_id but
  different checksums are distinct, independently-indexed data. A second
  aligned revision of an already-exported episode_id (added via an
  incremental export, §7) is exactly this case, handled with zero special
  casing anywhere in the read path.

Shard
= object-count / physical-write granularity
  One physical Parquet object holding a bounded, ordered subset of
  EpisodeRefs for one logical table (learning_steps or learning_signals).
  Bounded by ShardPolicy (max_episodes_per_shard / max_rows_per_shard,
  default_shard_policy() = 200 episodes / 200,000 rows). A shard is
  either written once and never touched again (full export), or reused
  verbatim forever once written (incremental export, §7) -- it is never
  partially rewritten.

Row group
= EpisodeRef read-locality granularity
  Exactly one Parquet row group per episode, within its shard. This is
  what makes a single-EpisodeRef selective read (§5) possible at all --
  it is the finest-grained unit PyArrow can address without reading
  neighboring episodes' bytes.

Manifest
= physical layout / index source of truth
  LearningDataExportManifest is the only place physical layout is
  recorded: table_uris (learning_episodes), shard_index
  (LearningDataShardIndex, learning_steps/learning_signals), inputs (the
  full exposed EpisodeRef set), and base_export_id
  (lineage only, §6). A reader never infers layout from listing an
  ArtifactStore prefix or from any row-level column (§6).

SceneOpsDataset
= semantic access boundary
  The only object anything downstream (SequenceSampler, external
  adapters, ad hoc consumers) ever talks to. Dispatches once, at open(),
  on manifest.shard_index's presence (v2-sharded vs v1-single-file, §11)
  -- never re-checked per call. Every access-strategy/cache decision in
  this document (§5, §6) is internal to this boundary; nothing above it
  changes based on physical layout, incremental-vs-full export history,
  or shard fragmentation.
```

## 4. Physical Parquet layout

- **`learning_episodes`** stays single-file, always — one row per exposed
  EpisodeRef, always metadata-scale (never more than a few hundred KB
  even at 10,000+ episodes, §9). Sharding it would add manifest/read
  complexity for no locality benefit.
- **`learning_steps`/`learning_signals`** are sharded: bounded,
  multi-episode Parquet objects, one row group per episode
  (`ShardEpisodeMember.row_group_index`), written with explicit
  `compression="zstd", compression_level=3` (matching every other write
  path in this codebase — PyArrow's own default under-compresses this
  schema's low-cardinality columns).
- Shard assignment (`plan_episode_shards`/`plan_shards_for_entries`) is a
  pure, deterministic bin-pack over `(episode_id,
  aligned_artifact_checksum)`-sorted entries, keyed on each episode's
  `step_count` as the sizing proxy for both tables (a documented
  approximation for `learning_signals`, whose actual row count also
  depends on channel count — see the scaling record).
- Object naming embeds `export_id`/`table_name`/`shard_index` for human
  debuggability, but **the manifest, never the URI, is the source of
  truth** a reader relies on.

## 5. Read / access strategy

Two access modes exist, both scoped inside `SceneOpsDataset` — nothing
above it chooses between them:

- **Selective single-EpisodeRef reads**: one episode's own
  shard + row group is looked up in an in-memory index built once at
  `open()` (`_build_shard_lookup`, no ArtifactStore listing, no Parquet
  scanning) and fetched via targeted `ArtifactStore.read_range` calls
  (`parquet_range_reader.py`'s `_LazyRangeFile`, on-demand and
  memoized — PyArrow decides exactly which byte ranges it needs; nothing
  is pre-guessed). Measured: **121.6x fewer bytes** than a whole-table
  fetch for one episode at 10,000-episode scale, and the reduction factor
  *grows* with scale (§9).
- **Shard-aware bulk access**: for workloads needing
  most/all of a large contributing set at once (`SequenceSampler.create()`'s
  schema resolution, `preload_episodes`), contributing EpisodeRefs are
  grouped by shard (`group_by_shard`) and each shard's needed row groups
  are fetched via one combined range read
  (`read_shard_row_groups_bulk`) instead of one independent selective
  fetch per episode. I/O call count then scales with **shard count**, not
  episode count — measured **58x fewer calls** (2,035 -> 35) at
  1,000-episode/6-shard scale, reconfirmed unchanged in shape at
  10,000-episode/51-shard scale (§9).
- `ArtifactStore.read_range(uri, offset, length)` is the
  one new capability both modes are built on — implemented for both
  `LocalArtifactStore` and `S3ArtifactStore`; every other `ArtifactStore`
  method is unchanged.

## 6. Cache policy

Three bounded, per-`SceneOpsDataset`-instance caches,
governed by `CachePolicy` (`max_episode_steps=64`, `max_schemas=128`,
`max_shard_metadata=256` by default; `0` disables, `None` means
unbounded, `DISABLED_CACHE_POLICY` exists for raw-cost benchmarking):

```text
_episode_steps_cache      EpisodeRef -> list[LearningStep]   (LRU, bounded)
_schema_cache             (EpisodeRef, projection) -> FeatureSchema   (LRU, bounded)
_shard_metadata_cache     (table_name, shard_index) -> pq.FileMetaData   (LRU, bounded)
```

**Purely a performance/reuse knob — never part of logical semantics.**
Two `SceneOpsDataset` instances opened over the same manifest with
different `CachePolicy` values return identical data from every method;
only what gets re-fetched vs. reused differs. Verified directly: after
exhaustively touching all 10,050 EpisodeRefs at 10,000-episode scale, all
three caches sat exactly at their configured bound (64 / 128 / 102 of
256), never grew unbounded.

## 7. Incremental export semantics

`EXPORT_LEARNING_DATA` supports two modes, coexisting —
incremental is not a replacement for full export:

- **Full export** (`base_export_id` unset):
  builds and writes every requested table from scratch over the complete
  target EpisodeRef set.
- **Incremental export** (`base_export_id` set): pure *addition* only —
  the target set must be a superset of the base's own exposed set;
  dropping/replacing an EpisodeRef is not supported (use a full export
  instead, which remains available and unaffected). A new aligned
  revision of an already-exported `episode_id` is supported with zero
  special-casing, since `EpisodeRef` identity is always the full
  `(episode_id, checksum)` pair (§3).
  - **Shard reuse is append-only**: every one of the base's existing
    shards is reused verbatim (same `uri`/`checksum`/`size_bytes`) —
    never rewritten, never re-uploaded, never given a new
    `ArtifactRecord`. Only the delta's own new episodes are binned into
    freshly-written shards, numbered starting after the base's own
    shard count.
  - **`learning_episodes` is the one deliberate exception**: always
    fully rewritten as one new merged file (base's existing rows +
    delta), since it is metadata-scale and merge-by-concatenation is
    simpler and more auditable than in-place patching. Reused rows keep
    their *original* provenance columns unchanged (§8).
  - Planning (`plan_incremental_export`, pure, no I/O) and execution
    (`write_incremental_sharded_learning_tables`) are separate — the
    planner never accesses the DB or ArtifactStore, and is fully
    deterministic given the same base manifest + target set.
  - Atomicity: unchanged from full export — every write happens before
    the single `context.commit()` at the end; a failure at any point
    leaves the base export exactly as valid as before, and retries are
    deterministic since `export_id` is a pure content hash.
  - Measured: **1-9% of full-rebuild write bytes** for representative
    deltas (+10/+100/+10-revisions/mixed) at 1,000-episode scale,
    reconfirmed at 10,000-episode scale (§9).
- **`SceneOpsDataset` requires zero incremental-specific code.** A
  full-build export and an incrementally-derived export exposing the
  identical final EpisodeRef set produce byte-identical
  `episodes()`/`get_step()` results through the same, unmodified reader
  — proven directly by
  `test_full_build_and_incremental_export_expose_identical_logical_data`
 .

## 8. Provenance semantics (frozen)

`learning_episodes.export_id`, `.dataset_id`, and `.dataset_version` (the
per-row columns, not the manifest's own fields) mean **first-write
provenance** — "the export call that originally wrote this exact row's
bytes" — never "which export currently exposes this row." A base
export's rows, reused verbatim by any number of later incremental
exports, keep these three values pinned to the *base's own* identifiers
forever; they are never rewritten on reuse.

**The current, authoritative answer to "which export exposes this row"
is always the opened `LearningDataExportManifest` itself** — its own
`.export_id`, together with `.inputs` (which does grow correctly on
every incremental export). Confirmed directly, by reading the code: `
SceneOpsDataset` never reads these three per-row columns for anything —
only `learning_manifest.export_id` (the manifest's own field) is ever
consulted, for error messages and shard/table-set consistency checks,
never for row filtering or identity.

**No future reader or tooling may infer current export membership from
these three row-level columns.** This mirrors, at the row-column layer,
the same "reuse means never re-labeling" principle already applied at
the `ArtifactRecord` layer (§7) — a reused shard's `ArtifactRecord`
likewise stays permanently owned by the export that first wrote it.

## 9. Measured performance characteristics

The strongest results from each request, all measured against real
Parquet/`AlignedEpisodeArtifact` contracts (no mocked I/O), reconfirmed
at up to 10,000 episodes in the final scale validation:

| Dimension | Single-file baseline | Sharded layout (frozen) |
|---|---|---|
| **Read granularity** | One EpisodeRef access reads 100% of `learning_steps`+`learning_signals`, every scale | One EpisodeRef access reads only its own shard's row group — **121.6x fewer bytes** at 10,000-episode scale; the reduction factor *grows* with scale (1.4x -> 3.6x -> 15.1x -> 121.6x) |
| **Bulk I/O scaling** | 1 selective fetch per contributing episode (2,035 calls at 1,000-episode/6-shard scale) | 1 combined fetch per contributing *shard* (35 calls, **58x fewer**); reconfirmed unchanged in shape at 10,000-episode/51-shard scale |
| **Cache growth** | `_episode_steps_cache`/`_schema_cache` grow with every distinct episode ever touched, no eviction | Bounded by `CachePolicy` (64/128/256 default) regardless of episodes touched — confirmed still exactly at-bound after touching **10,050** episodes (157x the cache's own size) |
| **Export cost model** | Any new/changed EpisodeRef rewrites 100% of `learning_steps`/`learning_signals` shards | Write amplification **0.010-0.092** (1-9% of full-rebuild bytes) for representative deltas at 1,000-episode scale; new-shard count stays flat regardless of base size |

**Current dominant bottlenecks**:

- **Read side**: pure Python/Pydantic `LearningStep` object-graph
  construction — **80-92% of per-episode cost** at every measured scale
  (6.6-32.7ms/episode), isolated directly from I/O+Parquet-decode cost
  (1.7-2.9ms/episode) via a dedicated measurement. I/O is never more than
  ~2% of total wall time for any read workload measured, at any scale.
- **Write side**: per-row, unvectorized Python table construction
  (`build_learning_steps_table`/`build_learning_signals_table`; unchanged since first measured).

**Storage, network round trips, and Spark/distributed processing are not
the current bottleneck for anything measured** — every workload's cost
resolved to a specific, already-understood Python/PyArrow/Polars call
(§10).

## 10. Distributed-processing boundary

- **Single-node PyArrow/Polars remains the production architecture.**
  Every workload measured in the scale validation (open, selective read,
  bulk read, full/exhaustive iteration, full export, incremental export)
  at up to 10,000 episodes / 94 MB / 104 objects resolved to a specific
  single-node bottleneck (§9) — never to "too much data for one machine."
- **Spark is not currently justified.** The one place the scale validation's own
  benchmark harness ran out of practical execution headroom (generating
  a 25,000-episode synthetic fixture) was traced to an already-known,
  unvectorized single-node Python loop (the *benchmark harness's* fixture
  generator, not the production write path) — exactly the kind of
  problem a vectorized single-node fix addresses, not the kind Spark
  exists to solve.
- **Distributed processing should be reconsidered only after** the
  measured single-node/vectorization opportunities this document already
  identifies (§9's read/write bottlenecks) have been exhausted, *and*
  measured CPU/memory/wall-clock requirements for a specific real
  workload still demand more than one machine can deliver. Concrete,
  measurement-based triggers (not an arbitrary dataset-size number):
  CPU-bound reconstruction time for one logical operation exceeding a
  caller's required wall-clock budget after vectorization; peak
  *working-set* memory (not total dataset size — `CachePolicy` already
  decouples those, §6) exceeding single-machine RAM for a required
  access pattern; or object/shard count growing enough to make the
  in-memory shard-lookup index or per-shard metadata caching itself the
  bottleneck (not observed at any scale measured in the scale validation).

No Spark dependency, scaffolding, or planning artifact exists anywhere in
this repository as of this freeze.

## 11. Production vs. legacy path status

**Production: v2-sharded is the only layout `EXPORT_LEARNING_DATA`
produces**, full or incremental, unconditionally —
verified directly against the job handler source (no params/branch
selects v1 output). `SceneOpsDataset.open()` dispatches to the v2
selective/bulk access path (§5) whenever `manifest.shard_index` is
present, which is always true for any manifest a production export
writes today.

**Legacy: v1-single-file support exists in `SceneOpsDataset` for
correctness/golden-fixture compatibility only** — it is not a second,
equally-current production architecture. It is exercised by the frozen
interop golden fixture (`interop_dataset.py`) and the adapter / entrypoint
tests built on it (`make lerobot-test`), which predate the sharded
layout and are deliberately kept on the v1 path as regression coverage — not
because any current production code path still writes v1 output. No
production job, API endpoint, or worker task other than tests/fixtures
reaches the v1 writer (`AnalyticsTableWriter.write_learning_table`) or
the v1-only reader fields (`SceneOpsDataset._steps_df`/`_signals_df`,
`_get_steps_df`/`_get_signals_df`). Kept, unmodified, as-is — removing it
would drop real regression coverage for the golden fixture path for no
production benefit, and this request's own scope excludes production
architecture changes.

## 12. Verification

- `make test`, `make lint`, `make test-integration`, `make lerobot-test` and
  the LeRobot container round trip of that time — all passing at freeze time (see
  [learning-data-scaling-baseline.md](../history/learning-data-scaling-baseline.md)
  §76 for the exact counts from the request that produced them; this
  freeze re-ran the same suite with zero production-code changes since).
- Architecture greps confirmed: no production code path writes a v2
  whole-table fetch (§5's selective/bulk paths are the only v2 read
  primitives); no `SceneOpsDataset` v2 cache is unbounded (§6); no Spark
  dependency exists in any `pyproject.toml`/`requirements`; `EXPORT_LEARNING_DATA`
  unconditionally produces the sharded layout (§11); the incremental
  reader-equivalence test (§7) proves the reader needs no
  incremental-specific code.

## 13. Intentional limitations

Deliberate boundaries, each with an explicit reason, not gaps that
"should" have been closed:

- **No compaction.** Append-only incremental export (§7) leaves many
  small trailing shards after many small increments — a purely
  quantitative, not correctness, concern; no correctness issue was ever found to justify building it.
- **No deletion/tombstones.** An EpisodeRef, once exposed by any export,
  cannot be removed via this architecture — by design; a "supersede/
  retract an episode" requirement needs new, not-yet-designed machinery.
- **No wide `learning_signals` redesign.** The long/tall
  (one-row-per-channel) layout means column projection narrows the final
  dense-vector assembly only, never what's fetched/parsed — unchanged; a wide-columnar redesign was never attempted.
- **No generic access-pattern optimizer.** Selective vs. bulk (§5) is a
  caller-chosen strategy, not auto-detected — deliberately not built,
  per the bridge's own constraint.
- **No distributed execution.** See §10.
- **No async-native PyArrow path.** The `run_coroutine_threadsafe`
  bridge reduced but did not eliminate a residual
  ~220-285us/call overhead against real MinIO; a fully async-native
  PyArrow integration would remove it further but was out of every
  request's scope (`ArtifactStore` staying a synchronous-call-friendly
  async API was a hard constraint throughout).

**Future optimization candidates** (evidence-backed, not started):

- **Lighter internal representation** for `LearningStep`/`AlignedSignal`
  reconstruction — the single most promising target given §9's
  isolated measurement (80-92% of per-episode read cost); scoped as an
  internal reconstruction-path change only, without touching either
  type's public/frozen shape.
- **Vectorized learning-table construction** — addresses the write-side
  bottleneck (§9), a real but separate target from the read-side one
  above.
- **Arrow-native processing further into the hot path** — the
  largest-scope candidate (deferring Python object construction past
  where it happens today); correctly out of scope for any single request
  so far as a "major semantic rewrite."
- **Optional compaction**, if shard fragmentation from many incremental
  deltas is ever measured to matter in practice (e.g. degraded
  `_shard_metadata_cache` hit rate under a real, heavily-incremented
  production dataset) — not needed today (§13 above).

## 14. Source-of-truth map

- Shard/manifest physical layout: `packages/sceneops-core/sceneops_core/episodes/learning_export/` (`sharding.py`, `schemas.py`, `identity.py`, `incremental.py`)
- Selective/bulk read implementation: `packages/sceneops-analytics/sceneops_analytics/learning_dataset/` (`dataset.py`, `parquet_range_reader.py`, `lru_cache.py`, `cache_policy.py`)
- Sharded/incremental writer orchestration: `packages/sceneops-analytics/sceneops_analytics/learning_tables_sharded.py`, `writer.py`
- `ArtifactStore.read_range`: `packages/sceneops-core/sceneops_core/artifacts/contracts.py`, `packages/sceneops-storage/sceneops_storage/backends/{local,s3}.py`
- `EXPORT_LEARNING_DATA` job handler: `apps/worker/sceneops_worker/jobs/dataset/export_learning_data.py`
- Full chronological record (every measurement, every bug found and
  fixed): [learning-data-scaling-baseline.md](../history/learning-data-scaling-baseline.md)
- Logical semantics this architecture does not change: [robot-learning-data.md](./robot-learning-data.md)
