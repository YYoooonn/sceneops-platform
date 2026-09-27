# Learning Data Scaling Baseline (Phase 5, Requests 5.1-5.4)

> Request 5.1: audit + measured baseline for `SceneOpsDataset`'s physical
> storage/query path (§1 below). Request 5.2: the sharded physical layout
> built on that baseline (§14 below). Request 5.3: selective
> EpisodeRef/window reads over that layout -- the read path Request 5.1
> measured and Request 5.2 made possible, finally exploited (§27 below).
> Request 5.4: bounded caches and a shard-aware bulk-access strategy on
> top of that selective path (§42 below). See
> [Robot learning data layer](./robot-learning-data.md) for the frozen
> Phase 2 domain contracts none of these requests touch, and
> [Storage layout](./storage-layout.md) for `ArtifactStore`/Parquet URI
> conventions.

## 1. End-to-end access path (as built, verified against source)

```text
EXPORT_LEARNING_DATA job (apps/worker/.../export_learning_data.py)
  -> build_learning_{episodes,steps,signals}_table (sceneops_analytics.learning_tables)
       pure Python: one dict per row, pl.DataFrame(rows, schema=...)
  -> AnalyticsTableWriter.write_learning_table (sceneops_analytics/writer.py)
       df.write_parquet(io.BytesIO())  -- polars defaults, no row_group_size,
       no sorting, no partitioning
  -> ArtifactStore.write_bytes(uri, data)  -- one whole-object PUT/write per table
  -> LearningDataExportManifest{table_uris, table_checksums, row_counts}

SceneOpsDataset.open(learning_manifest, artifact_store)
  -> ArtifactStore.read_bytes(learning_episodes.parquet)   -- WHOLE FILE, always
  -> pl.read_parquet(BytesIO(...))                         -- WHOLE TABLE into memory
  -> episode_refs / metadata_by_ref (dict), eager, at open()

dataset.get_step() / get_window() / resolve_feature_schema()
  -> _get_steps_df() / _get_signals_df()  (lazy, but only ONCE per dataset instance)
       -> ArtifactStore.read_bytes(learning_steps.parquet)    -- WHOLE FILE
       -> ArtifactStore.read_bytes(learning_signals.parquet)  -- WHOLE FILE
       -> pl.read_parquet(...) x2                              -- WHOLE TABLES into memory
  -> steps_df.filter(aligned_artifact_checksum == ref, [step_index == i])
       -- in-memory Polars filter, full-table scan, every call
  -> step_from_rows() / project_step() / project_sequence()  -- pure, per sceneops-core

SequenceSampler.create(dataset, projection, horizon, stride)
  -> per-episode window counts from EpisodeMetadata.step_count (no I/O)
  -> resolve_feature_schema() for EVERY contributing episode
       -- forces _reconstruct_all_steps() for every episode up front

SequenceSampler.get(i) / materialize_sequences()
  -> dataset.get_window() -- reuses per-episode step cache, cheap once warm

ExternalDatasetAdapter.export() (LeRobot etc.)
  -> per episode: dataset.get_window(ref, 0, step_count, projection)  -- one full-episode window
  -> never touches raw Parquet directly, sources only from SceneOpsDataset
```

**The "known historical limitation" in the request is confirmed still true
verbatim**: `SceneOpsDataset._get_steps_df`/`_get_signals_df`
(`packages/sceneops-analytics/sceneops_analytics/learning_dataset/dataset.py:310-324`)
each fetch their table **exactly once, in full**, on the first call that
needs *any* step/signal data for *any* episode, then every subsequent
access -- one step, one window, one episode, all episodes -- is answered by
an in-memory `pl.DataFrame.filter()` over that fully materialized table.
There is no lazy scan, no row-group pruning, no partition pruning anywhere
on this path.

## 2. Physical Parquet layout (as built, verified against source)

`AnalyticsTableWriter.learning_table_uri`
(`packages/sceneops-analytics/sceneops_analytics/writer.py:117-132`):

```text
{root}/{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_episodes.parquet
{root}/{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_steps.parquet
{root}/{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_signals.parquet
```

- **One export = one file per table.** Every EpisodeRef in an export lands
  in the same `learning_steps.parquet`/`learning_signals.parquet` -- there
  is no per-episode file, no per-episode row-group boundary guarantee, no
  Hive-style partitioning by any column.
- **`learning_signals` is the long/tall table**: one row per
  `(step, namespace, channel)` actually present, so its row count is
  `total_steps * channels_per_step` -- by construction the largest table by
  a wide margin (confirmed in every scale below: `learning_signals` is
  20-160x the size of `learning_steps` on disk).
- **Rows are episode-contiguous but not sorted or row-group-aligned.**
  `build_learning_steps_table`/`build_learning_signals_table`
  (`sceneops_analytics/learning_tables.py:142-236`) iterate `entries` in
  caller order and, within an entry, steps in index order -- so one
  episode's rows are contiguous today, but nothing enforces it (a curation
  merge of multiple exports, or a future re-ordering of `entries`, would
  silently break it), and `df.write_parquet(buffer)` is called with **no
  explicit `row_group_size`** -- Polars' default targets one row group for
  data this small, so even the current accidental contiguity buys nothing:
  every benchmark scale below produces effectively one row group per file,
  meaning Parquet's own min/max column statistics (which could otherwise
  let a reader skip whole row groups by `aligned_artifact_checksum`) have
  nothing to prune against yet.
- **`ArtifactStore.read_bytes(uri)` has no partial-read capability at
  all** (`packages/sceneops-storage/sceneops_storage/backends/local.py:49-56`,
  `.../backends/s3.py:104-112`): local is a whole-file `Path.read_bytes()`,
  S3/MinIO is a whole-object `GetObject` with no `Range` header support.
  This is a hard architectural floor: even if the physical layout were
  repartitioned and row-group-pruned tomorrow, nothing downstream of
  `ArtifactStore` could exploit it until `read_bytes` (or a new
  `read_range`/streaming method) exists.
- A general-purpose local DuckDB-over-Parquet helper already exists
  (`sceneops_analytics/query.py`'s `query_parquet`) but is unused by
  `SceneOpsDataset` and is explicitly local-filesystem-only (no S3/MinIO
  httpfs wiring) -- see [Storage layout](./storage-layout.md) §"Analytics
  (Parquet)".

## 3. Primary workloads identified

| Op | What it does today | I/O class |
|---|---|---|
| A. open dataset / enumerate EpisodeRefs | `pl.read_parquet` of `learning_episodes` only | metadata-only |
| B. fetch one episode revision (metadata) | dict lookup, already in memory after open() | none |
| C. fetch all steps for one EpisodeRef (cold) | triggers **whole** `learning_steps`+`learning_signals` load, then filter | **whole-table** |
| D. fetch one fixed-size window (warm) | in-memory `.filter()` over already-loaded tables | none (post-warm) |
| E. read selected feature channels | `FeatureProjection` narrows the *dense output*, not what's parsed off disk | whole-table (no reduction) |
| F. iterate many training windows | `SequenceSampler` -- schema-resolves (and thus fully reconstructs) **every contributing episode up front**, then cheap per-window slicing | whole-table, once |
| G. curate/filter episodes | pure in-memory filter over `learning_episodes` metadata | none |
| H. external dataset export | one full-episode `get_window()` per EpisodeRef, cold-opens the whole dataset once | whole-table, amortized over all episodes |

**A, B, G are metadata-only and already cheap at every scale measured.**
**C, D, F, H are the workloads that matter for Phase 5** -- they are what
actually touch `learning_steps`/`learning_signals`, and they are where
every measured bottleneck below lives. E matters only because it currently
buys nothing: an important negative result, not a workload class of its
own.

## 4. Benchmark harness (deterministic, non-production)

New, test/benchmark-support code only -- not imported by production code,
no pass/fail assertions, no wall-clock thresholds:

- `packages/sceneops-analytics/sceneops_analytics/testing/scale_fixture.py`
  -- a scaled sibling of the existing `testing/interop_dataset.py` golden
  fixture. Same real contracts (`AlignedEpisode`, `AlignedEpisodeArtifact`,
  `build_learning_*_table`, `AnalyticsTableWriter`), parameterized by
  `ScaleSpec(num_episodes, steps_per_episode, num_observation_channels,
  num_action_channels)`. Deterministic value formulas (no randomness);
  `expected_observation`/`expected_action` give an independent reference
  used by `test_scale_fixture.py`'s correctness smoke test (xs scale only,
  part of `make test`).
- `packages/sceneops-analytics/sceneops_analytics/testing/counting_artifact_store.py`
  -- `CountingArtifactStore`, a pass-through `ArtifactStore` wrapper that
  records `read_bytes`/`write_bytes` call counts and byte volumes per URI.
  Used to answer "how many files/bytes did this workload actually touch"
  directly, rather than inferring it from file sizes.
- `scripts/dev/benchmark_learning_data_scaling.py` -- the runnable harness
  (`uv run python scripts/dev/benchmark_learning_data_scaling.py`). Builds
  each scale point, then measures workloads A-H with `time.perf_counter`
  (wall time), `tracemalloc` (peak *Python-heap* allocation per phase --
  see caveat below), `resource.getrusage` (process RSS high-water mark),
  and `CountingArtifactStore` (bytes/files read). Prints a summary and
  optionally writes full JSON via `--out`. Not part of `make test`.

### Scale ladder

| scale | episodes | steps/ep | channels | total steps | total signal rows |
|---|---|---|---|---|---|
| xs | 4 | 50 | 10 (6 obs + 4 act) | 200 | 2,000 |
| s | 20 | 100 | 10 | 2,000 | 20,000 |
| m | 60 | 150 | 10 | 9,000 | 90,000 |
| l | 150 | 200 | 10 | 30,000 | 300,000 |

Chosen so the full ladder runs in ~80s total on a dev laptop while still
spanning two orders of magnitude in row count -- large enough to show the
scaling trend, small enough to iterate on. `l`'s `learning_signals` table
(300k rows) is still 1-2 orders of magnitude below a real production export
would be; see §9 for why the trend already visible here is expected to get
worse, not better, at real scale.

### Caveats on the memory numbers

- `tracemalloc` only traces CPython's own allocator (pymalloc) -- it does
  **not** see Polars/Arrow's native (Rust) buffers. So "traced peak" below
  isolates the cost of *reconstructing Parquet rows into Python/pydantic
  objects* (`LearningStep`/`AlignedSignal`/`SequenceSample`/the
  `_episode_steps_cache`), not the DataFrame's own backing memory --
  `ru_maxrss` is the only figure here that includes everything.
- `ru_maxrss` (`RUSAGE_SELF.ru_maxrss`) is a **whole-process, monotonically
  non-decreasing** high-water mark. All four scales ran in one process one
  after another, so each scale's reported value includes whatever the
  previous scales left resident -- read it as a coarse cumulative trend,
  not an isolated per-scale figure.

## 5. Baseline measurements

Table file sizes (on disk) and row counts, per scale:

| scale | learning_episodes | learning_steps | learning_signals | steps+signals bytes |
|---|---|---|---|---|
| xs | 7,805 B | 3,933 B | 18,412 B | 22,345 B |
| s | 7,928 B | 4,524 B | 74,265 B | 78,789 B |
| m | 8,133 B | 5,036 B | 265,469 B | 270,505 B |
| l | 8,053 B | 5,777 B | 907,252 B | 913,029 B |

Wall time per workload (seconds):

| scale | A open | C cold 1-episode | D warm 2nd-episode | E1 narrow-proj | E2 full-proj | F1 sampler.create (all eps) | F2 iterate all windows | H cold open+full export |
|---|---|---|---|---|---|---|---|---|
| xs | 0.0015 | 0.038 | 0.019 | 0.0187 | 0.0191 | 0.0018 | 0.023 (40 windows) | 0.102 |
| s | 0.0022 | 0.049 | 0.038 | 0.0335 | 0.0354 | 0.678 | 0.225 (400 windows) | 1.134 |
| m | 0.0173 | 0.082 | 0.060 | 0.0565 | 0.0604 | 3.750 | 1.015 (1,800 windows) | 5.160 |
| l | 0.0101 | 0.145 | 0.075 | 0.0833 | 0.0724 | 13.99 | 3.453 (6,000 windows) | 18.29 |

Bytes actually read off `ArtifactStore`, per workload (from
`CountingArtifactStore`):

| scale | A (open) | C (cold 1-episode) | D/E/F (warm, all) | H (cold, full export) |
|---|---|---|---|---|
| xs | 7,805 B (learning_episodes only) | 22,345 B | **0 B** | 30,150 B |
| s | 7,928 B | 78,789 B | **0 B** | 86,717 B |
| m | 8,133 B | 270,505 B | **0 B** | 278,638 B |
| l | 8,053 B | 913,029 B | **0 B** | 921,082 B |

`C`'s bytes-read figure equals **exactly** `steps+signals bytes` from the
table above, at every single scale -- fetching all steps for **one**
EpisodeRef reads **100% of the steps+signals tables**, regardless of
dataset size. `H`'s bytes-read figure equals the sum of all three table
sizes -- exactly once, no matter how many episodes are exported.

Traced Python-heap peak during `H` (cold open + full single-pass export),
and its ratio to on-disk `steps+signals` bytes:

| scale | traced peak (H) | steps+signals bytes | amplification |
|---|---|---|---|
| xs | 11.0 MB | 22,345 B | ~493x |
| s | 104.1 MB | 78,789 B | ~1,321x |
| m | 463.9 MB | 270,505 B | ~1,715x |
| l | 1,541.2 MB | 913,029 B | ~1,688x |

`ru_maxrss` after each scale (cumulative, whole-process, see caveat above):
xs 162 MB -> s 365 MB -> m 991 MB -> l 2,061 MB.

## 6. Memory/I/O observations

- **Every "touch one episode" op currently costs the same as "touch every
  episode."** `C` (cold, single EpisodeRef) reads 100% of `learning_steps`
  + `learning_signals` at every scale -- there is no way, today, to read
  less than the whole export to answer "give me one episode's steps."
- **Warm access is genuinely free of I/O** (`D`, `E1`, `E2`, and the bulk of
  `F`/`G` all show `0 B` incremental reads) -- the existing "fetch each
  table at most once per `SceneOpsDataset` instance" design does exactly
  what its docstring claims. The cost that remains after warming is 100%
  in-memory compute (Polars `.filter()` + Python object reconstruction),
  not I/O.
- **Column/feature projection buys nothing today.** `E1` (1 observation +
  1 action channel) and `E2` (all 10 channels) take statistically
  indistinguishable wall time at every scale (e.g. l: 0.0833s vs 0.0724s) --
  `_reconstruct_all_steps`/`step_from_rows` always parse every channel
  present in `learning_signals` into a `LearningStep` before
  `FeatureProjection` ever narrows anything. `learning_signals`' long/tall
  layout (one row per channel) means there's no column to *not* read in
  the first place -- narrowing which channels are wanted only changes the
  final dense-vector assembly, never what's fetched or parsed.
- **`SequenceSampler.create()` is the single most expensive workload
  measured, and it scales worse than linearly with episode count.**
  Because schema resolution runs `_reconstruct_all_steps()` for **every**
  contributing episode up front, `F1`'s cost is driven by
  `(num_episodes touched) x (total rows in the already-loaded table)` --
  each episode's `.filter()` re-scans the *entire* `learning_signals`
  table, not just its own slice. s->m->l shows this clearly: episodes grow
  3x/2.5x and signal rows grow 4.5x/3.3x per step, but `F1` wall time grows
  5.5x then 3.7x -- consistently worse than the episode-count ratio alone,
  confirming the per-call full-table rescan cost, not a fixed per-episode
  constant.
- **Reconstructing Parquet rows into Python/pydantic objects has a large,
  roughly constant (~1,600-1,700x at m/l scale) memory amplification
  factor** relative to on-disk (Parquet-encoded) bytes. This stabilizes as
  scale grows (xs's ~493x is dominated by fixed interpreter/import
  overhead; m/l converge near 1,700x), so it should be treated as a
  physical property of "one `AlignedSignal`+`LearningStep` pydantic object
  per signal row," not scale-dependent noise.
- **The writer side (`build_learning_signals_table`) is itself an
  unvectorized, pure-Python per-row loop** (`build_seconds`: xs 0.16s -> s
  1.26s -> m 6.02s -> l 21.8s, tracking row count roughly linearly) --
  out of scope for the *read* path this request audits, but worth flagging
  since Request 5.2+'s physical-layout change will need to touch this same
  builder.

## 7. Bottleneck classification

| Bottleneck | Category | Evidence |
|---|---|---|
| Whole-table fetch on first any-step-data touch | **Physical layout** + **ArtifactStore access** | §5 "C bytes read == 100% of steps+signals," every scale |
| No row-group/partition boundaries to prune against | **Physical layout** | §2: one file per table per export, default (≈single) row group, no sort/partition key |
| `ArtifactStore.read_bytes` has no range-read primitive | **ArtifactStore access** | `local.py`/`s3.py` both whole-object only -- a hard floor independent of Parquet layout |
| Repeated full-table `.filter()` per episode, no index | **Query execution** | §6 `F1` superlinear scaling with episode count x table size |
| `FeatureProjection` narrows output, not what's read/parsed | **Query execution** (column pushdown absent) | §6 E1 vs E2 indistinguishable wall time |
| `_episode_steps_cache`/`_schema_cache` grow unboundedly, never evicted | **Memory/cache** | `dataset.py:143-146` -- every episode ever touched stays resident for the `SceneOpsDataset` instance's lifetime; at real training scale (all episodes touched at least once by a full epoch) this converges to "the whole dataset materialized as Python objects, forever" |
| ~1,600-1,700x Python-object memory amplification over Parquet bytes | **Memory/cache** | §5/§6 traced-peak table |
| `learning_signals`' long/tall layout dominates row count | **Small-file/row-group behavior** (indirectly) | §2 -- one row per (step, channel); this is *why* row-group/partition strategy matters more here than for `learning_steps` |
| Unvectorized per-row Python table builder | **Physical layout (write path)**, out of this request's scope but adjacent | §6 `build_seconds` trend |

No small-file problem exists yet (one export = 3 files, always) -- that
changes if Request 5.2+ moves to per-episode or per-shard files, which is
exactly why §8 below weighs shard granularity explicitly.

## 8. PyArrow / Polars / DuckDB capability audit

Installed and already used elsewhere in the codebase (no new dependency
needed for 5.2+):

- **polars 1.43.2** -- currently used only for eager `pl.read_parquet` +
  `.filter()`. `pl.scan_parquet(path)` (lazy) with `.filter(pl.col(...) ==
  x)` pushed into the query plan would let Polars itself do predicate
  pushdown against row-group statistics **today**, with zero layout
  changes -- but only once `ArtifactStore` can hand Polars a local path or
  byte range instead of forcing "read the whole object into memory first."
  This is the cheapest available win but is capped by the `ArtifactStore`
  floor identified in §2/§4.
- **pyarrow 25.0.1** -- `pyarrow.dataset` (`ds.dataset(path,
  partitioning=...)`) supports Hive-style partition pruning (skip whole
  files by partition value, e.g. `episode_id=`/`aligned_artifact_checksum=`
  directories) and a `Scanner` with column projection + row-group-level
  predicate pushdown via Parquet statistics. This is the natural fit for
  "one EpisodeRef + step range + feature-channel projection" *if* the
  physical layout is repartitioned to make `aligned_artifact_checksum` (or
  `episode_id`) a partition/sort key.
- **duckdb 1.5.5** -- already wired for ad hoc local SQL
  (`sceneops_analytics.query.query_parquet`), unused by `SceneOpsDataset`.
  DuckDB's `read_parquet` also does row-group pruning against Parquet
  statistics and can push a `WHERE aligned_artifact_checksum = ...` down
  automatically; its main current gap (per `storage-layout.md` §3) is no
  S3/MinIO httpfs wiring -- local-file-only today.

None of the three needs to be "adopted wholesale" -- they overlap in
capability (all three can do row-group pruning against Parquet stats once
row groups actually align with an access key). The decision that matters
more than "which library" is **what the physical layout's partition/sort
key is**, because none of these libraries can prune what a single
unpartitioned, single-row-group file never separated in the first place.

## 9. At what scale would this actually break

Extrapolating §5/§6's trend (not a claim of exact asymptotics, just
direction): `F1`/`H` cost is driven by `episodes_touched x
total_signal_rows`, and a real production export is easily 100-1,000x `l`'s
300k signal rows (a few hundred episodes at a few thousand steps each, with
a realistic 20-40 channel schema, reaches tens of millions of signal rows
before any real robot fleet's worth of data). At that point:

- A single "fetch one episode" call still reads the *entire* multi-GB
  export -- becomes a multi-second-to-multi-minute cold read merely to
  answer one EpisodeRef, independent of whether the caller wanted step 0 or
  step 4,000,000.
- `SequenceSampler.create()`'s up-front full-dataset schema resolution
  (today's biggest measured cost) becomes the dominant startup cost of
  *every* training job, every time, because nothing caches it across
  process runs.
- The ~1,700x Python-object memory amplification means a dataset whose
  Parquet footprint is, say, 5 GB would require on the order of 8 TB of
  Python heap to fully materialize -- far past what fits in one process's
  memory, forcing partial/streaming access to become mandatory rather than
  an optimization.

## 10. Recommendations for Requests 5.2-5.6 (not implemented here)

- **Partitioned, not merely sharded, Parquet.** Partition by
  `aligned_artifact_checksum` (the EpisodeRef revision key) as a physical
  Hive-style directory/partition column for `learning_steps`/
  `learning_signals`, keeping `learning_episodes` as one small file (it
  already is, and always will be, metadata-scale). Partitioning by
  `episode_id` alone would incorrectly co-locate multiple revisions of the
  same episode; `aligned_artifact_checksum` is the actual EpisodeRef-unique
  key and must stay the partition key to preserve frozen revision
  semantics.
- **One EpisodeRef per file is the right granularity for
  `learning_steps`/`learning_signals`**, not multi-episode shards --
  workload C/D's access pattern is fundamentally per-EpisodeRef, and the
  measured bottleneck is precisely "one file holds every episode." A
  per-EpisodeRef file directly turns `C`'s "read 100% of the export" into
  "read this one EpisodeRef's own file" (bytes proportional to that
  episode's own step count, not the whole export). `learning_episodes`
  should stay one file (or one small partitioned set) since its whole
  point is cheap full enumeration at `open()`.
- **Row-group strategy**: even after per-EpisodeRef files exist, set an
  explicit `row_group_size` when writing (Polars/PyArrow both support this)
  sized so one episode's steps land in a small, bounded number of row
  groups sorted by `step_index` -- this is what makes workload D's future
  "one fixed-size window" query prunable by row-group statistics instead of
  requiring "read the whole episode file, then slice in memory" (today's
  `_reconstruct_all_steps` behavior even after layout changes, unless the
  read path also changes to use row-group-aware scanning).
- **PyArrow Dataset (not Polars eager, not DuckDB) as the primary read
  primitive**, once partitioning exists: `pyarrow.dataset` gives
  partition pruning (skip files by `aligned_artifact_checksum`) and
  row-group-level predicate pushdown (skip row groups by `step_index`
  range) as native, well-supported operations, and is the lowest-friction
  path to true column projection (only fetch the `learning_signals`
  columns a given `FeatureProjection` actually needs -- today's long/tall
  layout makes *row* filtering by `channel` the equivalent lever, since
  there's no per-channel column to project). Polars' `scan_parquet` can sit
  on top of the same files for the in-process filter/aggregate step this
  codebase already prefers (per `storage-layout.md`'s documented role
  split); DuckDB stays the ad hoc local-debugging tool it already is,
  unless S3/MinIO httpfs wiring becomes worth the investment for
  interactive analyst queries specifically.
- **Fixed-window access should become a `Scanner`-level row-group-range
  read**, not "reconstruct the whole episode, then slice a Python list" --
  this is the change that actually fixes workload D's *compute* cost
  (currently free of I/O but not of full-table-equivalent `.filter()`/
  object-construction cost) once per-episode files + sized row groups
  exist together; each is necessary but not sufficient alone.
- **`ArtifactStore` needs a range-read primitive before any of the above
  matters for S3/MinIO.** Add something like `read_range(uri, offset,
  length)` (or accept that Parquet-aware readers download-then-scan
  locally, the same restriction `query_parquet` already documents) --
  otherwise a `pyarrow.dataset`/DuckDB scan over an S3-backed store is
  still forced through a whole-object `GetObject` per file, which only
  matters once file granularity is small (per-EpisodeRef) rather than
  three giant per-export files.
- **Caching should move from "unbounded, per-`SceneOpsDataset`-instance,
  never evicted" to a bounded, explicit cache** (LRU by EpisodeRef, or
  simply "don't cache `_reconstruct_all_steps` results at all once
  per-EpisodeRef files make re-fetching cheap") -- §6/§9 both point at the
  current unbounded cache as the thing that turns "iterate the whole
  dataset once" into "materialize the whole dataset in memory forever,"
  which stops being viable once per-EpisodeRef reads are cheap enough that
  re-fetching is no longer the expensive part.
- **Incremental/backfill processing**: with per-EpisodeRef files, adding
  episodes to a `DatasetVersion`'s learning-data export becomes "write new
  per-EpisodeRef files + a new manifest referencing old + new files,"
  rather than today's "rebuild three giant files from scratch under a new
  `export_id`." This is the natural incremental unit precisely because
  `export_id` identity is already a pure function of the sorted aligned
  checksums (`learning_data_export_id`) -- adding one episode already
  produces a distinct, correctly-scoped `export_id` today; only the
  physical write (whole-table rebuild) needs to change to make that cheap.
- **Spark/distributed processing is not justified at any scale this audit
  can foresee for the *read* path.** §9's extrapolation (tens of millions
  of signal rows, single-digit-TB Python-object footprint) is a "fix the
  single-process read path" problem (PyArrow Dataset + partitioning +
  bounded caching solves it), not a "needs distributed compute" problem --
  a single process streaming per-EpisodeRef/per-row-group reads handles
  this comfortably. The *write/export* side (`build_learning_signals_table`
  building 10s of millions of Python row-dicts, §6) is closer to where
  distributed/vectorized rewrite would first become justified, but that is
  a Request 5.2+ builder-side concern, not something this read-path audit
  should scope.

## 11. Frozen semantics (unchanged by this request)

`EpisodeRef` identity, `aligned_artifact_checksum` revision semantics,
`learning_episodes`/`learning_steps`/`learning_signals` logical schemas,
ABSENT vs MISSING, `FeatureProjection`/`FeatureSchema`, `SequenceSampler`
window semantics, external adapter semantics, `Artifact`/lineage ownership
-- nothing above changes any of these; every recommendation in §10 is a
physical-representation change only.

## 12. Verification

- `make test` -- 1289 passed, 5 skipped
- `make lint` -- all checks passed
- `make lerobot-test` -- 36 passed
- `make test-integration` -- 35 passed (real Postgres/MinIO via `make
  local-up`)

## 13. Unknowns / blockers for 5.2+

- No measurement here reflects real S3/MinIO network latency -- everything
  above ran against `LocalArtifactStore` (filesystem). `S3ArtifactStore`'s
  per-call latency (network round trip per `read_bytes`/`GetObject`) will
  make "one file per EpisodeRef" a *more* attractive granularity (fewer
  round trips matter less once each is small) but also makes "many tiny
  files" a real small-file-request-count concern once EpisodeRef counts
  reach the tens of thousands -- Request 5.2 should benchmark
  `S3ArtifactStore`/MinIO specifically before finalizing shard granularity.
- This audit did not attempt a real `pyarrow.dataset`/DuckDB scan against
  the current (unpartitioned) layout to get an exact "how much would
  partition+row-group pruning save today" number -- §8/§10's
  recommendations follow from the measured access pattern and each
  library's documented capabilities, not from a working prototype. Request
  5.2 should build that prototype before committing to a specific
  partition/row-group scheme.
- The write-side (`build_learning_signals_table`'s per-row Python loop)
  cost trend (§6) was observed but not scoped as an in-depth bottleneck
  here -- flagged for whoever designs Request 5.2's new writer, since a
  partitioned layout necessarily touches this same code path.

---

# Request 5.2: Scalable Learning Storage Layout

Everything below is new; §1-13 above (Request 5.1) are historical record
and unchanged. Physical layout only -- no logical semantics changed (§20).
The *reader*'s access strategy (eager whole-table fetch, in-memory filter)
is unchanged by design (§16/§21) -- Request 5.3 owns making it selective.

## 14. Scale-fixture changes

Extended `packages/sceneops-analytics/sceneops_analytics/testing/
scale_fixture.py` (the same module Request 5.1 added) with deterministic
realistic-variation knobs on `ScaleSpec`, all defaulting to "off" so the
exact Request 5.1 behavior is still reachable:

- `length_jitter_fraction` -- per-episode step_count varies by a bounded,
  deterministic formula (`_step_count_for`), not a fixed constant per scale.
- `extra_revision_every` -- every Nth logical `episode_id` gets a second
  aligned revision (distinct `aligned_artifact_checksum`, disjoint content
  via a `1e9`-scaled base offset) -- proves `EpisodeRef` identity
  (`episode_id` + `aligned_artifact_checksum`, never `episode_id` alone)
  survives sharding.
- A cycling task/outcome distribution (`pick/place/insert/calibrate`,
  `SUCCESS/SUCCESS/FAILURE/UNKNOWN`) instead of one constant value.
- `num_core_observation_channels`/`num_core_action_channels` split each
  namespace into "core" (always present, always `RESOLVED`) and "extra"
  channels. Extra channels are deterministically ABSENT from some
  episodes entirely (`_extra_channel_present`) and deterministically
  `MISSING` on some steps where present (`_extra_channel_step_missing`) --
  core channels are never either, so `feature_projection_for()`'s
  core-only `FeatureProjection` stays projectable at every scale.
- Channel 0 in each namespace is scalar-valued (`NUMERIC_SCALAR`), the
  rest are 3-vectors -- mirrors `interop_dataset.py`'s
  `gripper_position`/`gripper_command` scalar-among-vectors mix.

`interop_dataset.py` (the frozen golden fixture) is untouched -- it still
uses the single-file writer path unchanged, per this request's own
instruction to keep it separate. New unit coverage added:
`packages/sceneops-analytics/tests/test_scale_fixture.py` gained 6 tests
(length jitter, multi-revision, task/outcome cycling, ABSENT/MISSING
isolation on extra channels only, an end-to-end core-projection
round-trip over the full varied spec, and shard-policy splitting) -- all
fast (`VARIED_SPEC` uses 6 episodes), part of `make test`.

`write_scaled_dataset_artifacts()` now writes through the new sharded
writer (§15) instead of the old single-file `write_learning_table` for
`learning_steps`/`learning_signals` (`learning_episodes` is unaffected --
never sharded, see §17).

### The scale ladder

| tier | episodes | steps/ep | channels (obs+act) | core channels | jitter | revision period |
|---|---|---|---|---|---|---|
| tiny | 10 | 60 | 6+4 | 4+3 | 30% | every 4th |
| small | 100 | 100 | 8+5 | 5+3 | 30% | every 10th |
| medium | 1,000 | 60 | 8+5 | 5+3 | 30% | every 25th |
| large | 10,000 | 30 | 6+4 | 4+3 | 30% | every 200th |

`large` deliberately keeps `steps_per_episode`/channel-count small rather
than repeating `medium`'s shape at 10x the episodes -- the physical-layout
question this tier exists to answer is small-file risk at high **episode
count**, not total byte volume, and the existing per-row Python table
builders (`build_learning_steps_table`/`build_learning_signals_table`,
Request 2.5, unmodified by this request) make a naively bigger `large`
tier impractical to build in this environment (§25). Actual EpisodeRef
counts after revisions: tiny=13, small=110, medium=1,040, large=10,050.

## 15. Candidate physical layout comparison

Both candidates are the *same* mechanism at different settings --
`plan_episode_shards()` (pure, sceneops-core) bins a sorted
`(EpisodeRef, step_count)` sequence into shards bounded by
`ShardPolicy(max_episodes_per_shard, max_rows_per_shard)`. Setting
`max_episodes_per_shard=1` reproduces "episode-per-file" as a special
case -- no second code path exists, so the comparison below is a true
apples-to-apples sweep of one parameter, not two implementations.

Measured via `scripts/dev/benchmark_learning_data_layout.py` (new,
non-production, no pass/fail assertions):

| scale | policy | total objects | steps shards | steps size (min/mean/max) | signals shards | signals size (min/mean/max) | episodes/shard (mean/max) |
|---|---|---|---|---|---|---|---|
| tiny (13 refs) | episode-per-file | 27 | 13 | 3,014 / 3,122 / 3,226 B | 13 | 9,555 / 11,106 / 13,404 B | 1 / 1 |
| tiny | bounded-default | 3 | 1 | 29,910 B | 1 | 115,960 B | 13 / 13 |
| small (110 refs) | episode-per-file | 221 | 110 | 3,199 / 3,396 / 3,630 B | 110 | 11,525 / 15,958 / 21,728 B | 1 / 1 |
| small | bounded-default | 3 | 1 | 277,171 B | 1 | 1,496,859 B | 110 / 110 |
| medium (1,040 refs) | episode-per-file | 2,081 | 1,040 | 3,015 / 3,135 / 3,267 B | 1,040 | 9,883 / 11,441 / 18,210 B | 1 / 1 |
| medium | bounded-default | 13 | 6 | 90,904 / 391,140 / 451,564 B | 6 | 378,628 / 1,576,862 / 1,863,429 B | 173.3 / 200 |
| large (10,050 refs) | episode-per-file | 20,101 | 10,050 | 2,856 / 2,913 / 2,974 B | 10,050 | 8,196 / 9,704 / 11,097 B | 1 / 1 |
| large | bounded-default | 103 | 51 | 102,296 / 400,842 / 406,927 B | 51 | 380,002 / 1,448,299 / 1,513,909 B | 197.1 / 200 |

Comparison against the axes requested:

- **Object count**: episode-per-file grows linearly and unboundedly with
  EpisodeRef count (20,101 objects at 10,050 refs); bounded-default caps
  at ~2 objects per 200 episodes (103 objects at the same scale -- a
  ~195x reduction).
- **Object-size distribution / small-file risk**: episode-per-file's
  per-object sizes stay in the 3-20 KB range at every scale, well inside
  "small file" territory for any object store (S3/MinIO request overhead,
  metadata-operation cost, and listing/inventory cost all become
  significant relative to payload size at this granularity, per §26's
  unknown but directionally-expected S3-latency concern from Request
  5.1 §13). Bounded-default's shards stay in the 100 KB-2 MB range at
  every scale tested -- comfortably away from small-file territory
  without needing per-episode addressing to give it up entirely (row
  groups still do, see §16).
- **EpisodeRef lookup/read amplification**: see §18 -- bounded-default's
  *shard-level* amplification (95x-329x at the scales tested) is already
  large; episode-per-file's would be ~1x by construction (trivial: the
  file already contains it) but that's precisely the property that
  causes the object-count explosion above -- there is no free lunch here,
  only where you put the granularity.
- **Row-group pruning potential**: identical for both candidates in
  principle (both use one-row-group-per-episode within whatever file a
  given episode lands in, §16) -- the axis that actually differs between
  candidates is object count/size, not row-group granularity.
- **Incremental-write behavior**: episode-per-file's per-episode files
  make appending N new episodes to an existing export trivial (write N
  new files, extend the shard index) with zero rewrite of existing
  objects. Bounded-default requires either accepting an under-full last
  shard (simple, what this implementation does) or rewriting the last
  shard to top it up (more complex, not implemented) -- a real, if minor,
  incremental-write cost bounded-default pays that episode-per-file
  doesn't.
- **Manifest/index complexity**: identical -- both use the same
  `LearningDataShardIndex` shape (§19); episode-per-file just happens to
  produce one-entry shards.
- **Object-store suitability**: bounded-default is the clear fit given
  the above -- avoids the small-file/object-count explosion that would
  otherwise dominate S3/MinIO request costs at real fleet scale, at the
  cost of a real but bounded read-amplification floor (§18) that Request
  5.3's row-group-level reads can substantially shrink further (§18).

**A real, measured cost of bounded-default not visible in the table
above**: because bounded shards hold many episodes' rows in one file,
Parquet writing them still uses one row group per episode (§16) --
combined with `PyArrow`'s writer (needed for row-group control; Polars'
own writer doesn't expose per-call row-group boundaries), the resulting
bytes are measurably larger than the old single-row-group-per-file layout
would produce for the *same logical content*: at `small` scale, the same
110-episode entries produce 369,530 bytes (steps+signals) in the old
single-file/single-row-group layout vs. 1,782,983 bytes bounded-default
sharded -- a ~4.8x size increase, isolated and explained in §16. This
does not change the layout *choice* (bounded shards still avoid the
object-count explosion either way) but is a real storage-cost trade-off
of the row-group strategy within it, reported honestly rather than
hidden.

## 16. Selected layout and rationale

**Selected: bounded multi-episode shards (`max_episodes_per_shard=200,
max_rows_per_shard=200_000`, `default_shard_policy()`), with one Parquet
row group per episode within each shard.** Not episode-per-file, despite
Request 5.1's own §10 leaning that direction before this request's
measurements existed -- §15's object-count explosion at 10,000+ episodes
is the deciding factor Request 5.1 hadn't measured yet, and the task's own
warning ("do not choose episode-per-file merely because it is easiest at
current scale") is borne out by real numbers here, not just intuition.

Rationale, in order of weight:

1. **Object-count/small-file risk dominates at real scale.**
   Episode-per-file's per-object sizes (3-20 KB) are exactly what object
   stores handle worst per-request; a 10,000-episode export producing
   20,101 objects is a small-file-explosion regime a fleet-scale dataset
   (order of magnitude beyond this benchmark's `large` tier) would make
   considerably worse, not better.
2. **Bounded shards still deliver most of the locality benefit.**
   Episode-aligned row groups within a shard (below) mean a future
   selective reader (Request 5.3) can address one episode's exact rows
   inside a shard without touching neighboring episodes' bytes -- the
   *shard* is the unit of object-count control, the *row group* is the
   unit of read-locality control, and this design gets both.
3. **The measured storage-overhead cost (§15's ~1.9-4.8x size increase)
   is real but bounded and independent of scale** -- it comes from
   per-episode row-group fragmentation (§ below) and a PyArrow-vs-Polars
   writer difference, not from the shard-count choice itself, and is a
   fixable/tunable follow-up (§25), not a reason to abandon
   episode-aligned row groups.
4. **Incremental-write cost (§15) is real but minor** relative to the
   object-count risk it avoids -- an under-full last shard is a
   reasonable, simple default; more sophisticated shard-rebalancing on
   backfill is future work (§24), not required for this request.

### Isolating the size overhead (why bounded-default's files are bigger)

Two independent, measured causes, using the real `small`-scale
`learning_signals` shard (123,384 rows) as the isolation case:

| writer | row groups | compression | bytes |
|---|---|---|---|
| Polars `write_parquet` (legacy, unchanged) | 1 | zstd level 3 (Polars default) | 353,809 |
| PyArrow `ParquetWriter` | 1 | zstd level 3 | 716,020 |
| PyArrow `ParquetWriter` | 110 (one/episode) | zstd level 3 | ~1,475,705 (measured with default compression before the fix below; see note) |

- **Cause A -- writer/codec defaults**: `pyarrow.parquet.ParquetWriter`
  defaults to `compression="snappy"` at an effective level equivalent to
  zstd level 1; Polars' `write_parquet` defaults to `compression="zstd",
  compression_level=3`. **Fixed** in this request
  (`AnalyticsTableWriter.write_learning_table_shard` now passes
  `compression="zstd", compression_level=3` explicitly, matching Polars'
  documented default exactly) -- this alone closed roughly half the
  originally-observed gap (see the commit history in this file: the
  layout-comparison numbers in §15 already reflect the fixed codec).
- **Cause B -- row-group fragmentation**: even with identical
  compression settings, one row group per episode (110 row groups)
  compresses measurably worse than one row group for the whole shard,
  because dictionary/statistics overhead is paid per row group and this
  schema's several low-cardinality string columns (`namespace`,
  `channel`, `policy`, `status`, `value_kind`, and the constant
  `dataset_id`/`dataset_version`/`export_id` columns) lose cross-episode
  dictionary sharing when split into many small row groups.
- **A residual gap remains between PyArrow's writer and Polars' native
  writer even at one row group each** (716,020 vs 353,809 bytes above,
  ~2x) -- not fully explained; Polars uses its own native Rust Parquet
  writer (not a PyArrow wrapper), and likely applies additional
  encoding-level optimizations (e.g. `BYTE_STREAM_SPLIT` for
  floating-point columns) PyArrow's writer doesn't enable by default.
  Chasing this further was out of this request's scope (choosing a
  shard/row-group *policy*, not tuning Parquet encoding internals) -- see
  §25.

This is reported as a known, measured, and accepted trade-off: precise
per-episode row-group addressability (needed for Request 5.3's
selective reads, §24) costs roughly 2-5x storage overhead relative to a
single-row-group-per-file layout at the scales measured. A coarser
row-group granularity (e.g. batching several episodes per row group)
would recover some of this cost at the price of pruning precision -- a
viable follow-up tuning direction (§25), not implemented here since the
task's own framing (`"episode-aligned row groups where feasible"`)
favors precision for this request.

## 17. Files changed

**sceneops-core** (pure domain, no I/O):
- `packages/sceneops-core/sceneops_core/episodes/learning_export/sharding.py`
  (new) -- `ShardPolicy`, `ShardEpisodeMember`, `LearningDataShard`,
  `LearningDataShardIndex`, `default_shard_policy()`,
  `plan_episode_shards()` (pure bin-fill).
- `.../learning_export/schemas.py` -- `LearningDataExportManifest` gained
  `layout_version: str` and `shard_index: LearningDataShardIndex | None`
  (both additive, default `None`/`"v1-single-file"` -- old manifests
  unaffected).
- `.../learning_export/__init__.py` -- new exports.
- `packages/sceneops-core/sceneops_core/jobs/schemas/results/episodes.py`
  -- `ExportLearningDataJobResult` gained `shard_counts: dict[str, int]`.

**sceneops-analytics** (I/O, the new writer + reader generalization):
- `sceneops_analytics/writer.py` -- `AnalyticsTableWriter` gained
  `learning_table_shard_uri()`/`write_learning_table_shard()` (PyArrow
  `ParquetWriter`, explicit `row_group_size` per call, `zstd`
  level 3). Old `write_learning_table()`/`learning_table_uri()`
  unchanged, still used for `learning_episodes` and by the frozen
  `interop_dataset.py` fixture.
- `sceneops_analytics/learning_tables_sharded.py` (new) --
  `plan_shards_for_entries()`/`write_sharded_learning_tables()`: the one
  orchestration entry point shared by the production job handler and the
  scale fixture (sorts entries, plans shards, reuses
  `build_learning_steps_table`/`build_learning_signals_table` unchanged
  per shard, writes each shard, assembles `LearningDataShardIndex`).
- `sceneops_analytics/learning_dataset/dataset.py` -- `SceneOpsDataset`
  gained `_table_shard_uris()`/`_read_parquet_tables()`: resolves either
  `table_uris[name]` (legacy, one URI) or `shard_index.<name>` (new, N
  URIs) and concatenates via `pl.concat` -- the eager
  fetch-once-then-cache-then-filter *model* is completely unchanged, only
  "how many URIs back this table" generalizes from 1 to N.
- `sceneops_analytics/__init__.py` -- new exports.
- `sceneops_analytics/testing/scale_fixture.py` -- realistic variation +
  sharded writer (§14).
- `sceneops_analytics/testing/__init__.py` -- new exports.

**apps/worker** (production job handler):
- `sceneops_worker/jobs/dataset/export_learning_data.py` --
  `learning_episodes` still single-file; `learning_steps`/
  `learning_signals` now go through `write_sharded_learning_tables()`
  with `default_shard_policy()`. One `ArtifactRecord` per physical shard
  file added (lineage), alongside the existing per-table/manifest
  records.

**Tests**:
- `apps/worker/tests/jobs/test_export_learning_data_handler.py` -- mocks
  updated from `write_learning_table` to `write_learning_table_shard` for
  steps/signals; assertions updated for the new `table_uris`/
  `shard_counts` split.
- `packages/sceneops-analytics/tests/test_scale_fixture.py` -- 6 new
  tests (§14).

**Benchmarks** (new, non-production):
- `scripts/dev/benchmark_learning_data_layout.py` -- layout comparison +
  selective-read potential + old-vs-new access-pattern re-run (§15/§18/§21).

**Untouched, intentionally** (§20): `interop_dataset.py`,
`scripts/e2e/e2e_fixture_bootstrap.py`, every hand-rolled test fixture in
`test_learning_dataset.py`/`test_sequence_sampler.py`/
`test_torch_adapter.py`/`test_external_adapters.py`/
`test_learning_consumer_adapter.py` -- all still build the legacy
single-file layout directly via `write_learning_table`, and all still
pass unmodified (§26).

## 18. Shard assignment policy

`plan_episode_shards()` (pure, `sceneops_core.episodes.learning_export.
sharding`): entries are sorted by `(episode_id, aligned_artifact_checksum)`
-- the same canonical order `SceneOpsDataset.episodes()` already returns
regardless of physical layout (frozen, Request 2.7B §2), so this
reordering relative to the legacy writer's caller-order is never
consumer-visible. A simple sequential greedy bin-fill then closes the
current shard as soon as adding the next episode would exceed either
`max_episodes_per_shard` or `max_rows_per_shard` (evaluated against each
episode's `learning_steps` row count as a shared proxy for both tables,
§ note on `ShardPolicy`'s docstring) -- not an optimal bin-packing, and
never reorders episodes to pack tighter. A single episode whose own row
count exceeds `max_rows_per_shard` becomes a one-episode shard rather than
being split (Parquet row groups require contiguous, undivided rows).

Production default (`default_shard_policy()`): `max_episodes_per_shard=200,
max_rows_per_shard=200_000` -- chosen from §15's measurements so a
10,000-episode export still produces on the order of 50 shards per table
(never one file per episode) while keeping each shard's absolute size in
the 100 KB-2 MB range (comfortably above small-file territory, comfortably
below "large object" territory for interactive tooling). The row bound is
deliberately generous relative to a typical episode's step_count so
episode count, not row count, is the usual binding constraint -- the row
bound exists specifically to protect against a minority of unusually long
episodes.

## 19. Row-group strategy

One Parquet row group per episode, within whichever shard that episode
was assigned to (`AnalyticsTableWriter.write_learning_table_shard`,
via `pyarrow.parquet.ParquetWriter.write_table(slice, row_group_size=
exact_slice_length)` called once per episode) -- verified directly against
real Parquet metadata (not just the writer's own bookkeeping): every
shard's `pq.ParquetFile(path).metadata.num_row_groups` equals its episode
count, and each row group's `num_rows` matches that episode's own row
count exactly (confirmed in this request's manual verification and in
`test_shard_policy_splits_episodes_across_multiple_shards`).

Both `learning_steps` and `learning_signals` shards for a given
`shard_index` share the identical episode order (same
`plan_shards_for_entries()` call decides both), so `row_group_index`
means the same EpisodeRef in both tables at that shard -- this invariant
is what lets `LearningDataShard.episodes` (an ordered list, no separate
reverse index needed) answer "which row group is EpisodeRef X in this
shard" by simple list position, for both tables uniformly.

Cost of this choice: quantified and discussed in §16 -- roughly 2-5x
storage overhead vs. a single-row-group-per-file layout at the scales
measured, accepted as the price of precise future row-group-level
addressability (§24).

## 20. Manifest/index changes

`LearningDataExportManifest` (sceneops-core, additive, backward
compatible -- old manifests default to the pre-5.2 meaning unchanged):

```python
layout_version: str = "v1-single-file"   # or "v2-sharded" -- informational only
shard_index: LearningDataShardIndex | None = None
```

`LearningDataShardIndex`:

```python
shard_policy: ShardPolicy                       # the bounds that produced this layout
learning_steps: list[LearningDataShard]         # empty if that table wasn't requested/sharded
learning_signals: list[LearningDataShard]
```

`LearningDataShard`: `shard_index`, `uri`, `checksum`, `size_bytes`,
`row_count`, and `episodes: list[ShardEpisodeMember]` (ordered,
`row_group_index` == position in this list). `ShardEpisodeMember`:
`episode_ref`, `row_group_index`, `row_count` (this episode's exact row
count in *this* table -- steps vs. signals differ).

This answers all three questions Request 5.2 §4 asked for:

- **Which objects belong to each logical table** -- `table_uris["learning_
  episodes"]` (always) plus `shard_index.learning_steps`/
  `.learning_signals` (when sharded) -- never object-store listing.
- **Which shard contains a given EpisodeRef** -- linear scan over
  `shard_index.<table>` (a small, all-metadata list -- at most ~50 shards
  at the scales measured) checking each shard's `episodes` list; no
  reverse index was added since this scan touches only manifest metadata,
  never Parquet bytes (§24 hands this exact lookup to Request 5.3).
- **Row-group/locality metadata** -- `row_group_index` per member, exact
  by construction (§19).

`learning_episodes` is **not** sharded -- it stays in `table_uris` exactly
as Request 2.5 defined it (always metadata-scale, one row per EpisodeRef;
sharding it would add manifest complexity for no locality benefit, per
this request's own "avoid a partition-directory cardinality proportional
to every unique checksum unless measurements justify it" guidance). No DB
catalog, no generic partition registry -- the manifest alone is the index,
as required.

## 21. Object/file naming

```text
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_episodes.parquet   (unchanged)
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/manifest.json               (unchanged)
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_steps/shard-00000.parquet
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_steps/shard-00001.parquet
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_signals/shard-00000.parquet
...
```

(`AnalyticsTableWriter.learning_table_shard_uri`.) Shard files nest under a
per-table subdirectory so a human/tool listing the export's tree sees
"one directory per table" rather than hundreds of files flattened
together with the manifest -- purely for debuggability; the manifest, not
this path structure, is what any reader must actually use to enumerate
shards.

## 22. Logical-schema preservation

Unchanged, verified by construction and by tests:

- `build_learning_steps_table`/`build_learning_signals_table` (Request
  2.5) are called **completely unmodified** -- once per shard, over that
  shard's entries subset, never touched by this request. The column set,
  types, and per-row semantics are byte-identical to what the legacy
  single-file writer produces for the same entries.
- ABSENT vs. MISSING: unchanged (§14's new ABSENT/MISSING variation
  exercises the *existing* frozen contract via realistic data, it does
  not add a new state or change how either is represented).
  `test_extra_channel_goes_absent_and_missing_but_core_never_does`
  verifies both states are producible and that core channels never
  exhibit either.
- `EpisodeRef` identity (`episode_id` + `aligned_artifact_checksum`):
  unchanged; `test_scaled_fixture_exposes_every_episode_ref`-style checks
  and the new multi-revision test confirm two revisions of one
  `episode_id` remain distinct `EpisodeRef`s through the sharded path.
- `FeatureProjection`/`FeatureSchema`, `SequenceSampler` window semantics,
  external adapter semantics: unchanged and exercised unmodified by the
  full existing test suite (§26) plus `make e2e-lerobot-container`'s real
  container round-trip (§26), all passing against the new production
  writer path.

## 23. Old-layout cleanup

No cleanup performed, deliberately: the old single-file writer
(`write_learning_table`/`learning_table_uri`) and the corresponding reader
path (`table_uris[name]` resolution in `SceneOpsDataset`) are **kept**,
unmodified, because:

- `interop_dataset.py` (the frozen correctness golden fixture) uses it and
  must keep doing so -- Request 5.2's own instruction not to replace
  correctness fixtures with scale fixtures.
- `scripts/e2e/e2e_fixture_bootstrap.py` (the persistent E2E "interop"
  fixture backing `make e2e-lerobot`/`make e2e-lerobot-container`) uses it
  -- changing it risks perturbing a golden LeRobot round-trip comparison
  for no physical-layout benefit, since that E2E's subject is adapter
  correctness, not storage layout.
- Five more test files (`test_learning_dataset.py`,
  `test_sequence_sampler.py`, `test_torch_adapter.py`,
  `test_external_adapters.py`, `test_learning_consumer_adapter.py`) build
  fixtures inline via the same old writer -- none of them are testing
  physical layout, so there is no correctness reason to migrate them, and
  every one of them still passes unmodified (§26).

This is the practical instantiation of "prefer one clean production
layout" (the production `EXPORT_LEARNING_DATA` job now writes only the
new sharded layout) without requiring "backward-compat-free" to also mean
"delete the single-file code path" -- that path remains because other,
unrelated things still legitimately depend on it, not because of a
compatibility guarantee to external consumers.

## 24. Requirements handed to Request 5.3

Request 5.3 owns turning §15-19's layout into actual selective I/O. What
it needs, already in place:

- **`ArtifactStore` needs a range-read primitive.** `read_bytes(uri)` is
  still whole-object-only on both backends (Request 5.1 §2, unchanged) --
  nothing above helps until something like `read_range(uri, offset,
  length)` exists, or 5.3 accepts "download whole shard, then use
  row-group metadata to slice in memory" as an interim step (shard-level
  selectivity without byte-range selectivity).
- **A `LearningDataShardIndex`-aware lookup**: given an `EpisodeRef`, scan
  `shard_index.<table>` (a handful to ~50 entries at realistic scale) to
  find `(shard, member)`; `member.row_group_index` is then the exact
  `pyarrow.dataset`/`ParquetFile` row group to fetch. §20 intentionally
  left this as "scan the manifest," not a reverse index -- 5.3 may want a
  `dict[EpisodeRef, ...]` built once at `open()` time for O(1) repeated
  lookups, which is a trivial addition over the data already present.
- **PyArrow Dataset/Scanner, not DuckDB, as the primary read primitive**
  (per Request 5.1 §10's unchanged recommendation) -- partition pruning by
  shard file (skip whole shards whose manifest entry doesn't contain the
  target `EpisodeRef`) plus row-group-level predicate pushdown (skip row
  groups by `step_index` range within a shard, once 5.3 also sorts/bounds
  steps within an episode's own row group, which today's per-episode
  row-group design already guarantees implicitly -- one row group already
  *is* one episode's full step range).
- **`SceneOpsDataset`'s current eager-whole-table-per-instance cache
  (`_steps_df`/`_signals_df`, unchanged, §17) is the thing 5.3 must
  actually replace** -- §21's re-run confirms today's reader still
  concatenates every shard for a table on first need, so none of §18's
  measured amplification is realized yet; that gap is exactly what 5.3
  closes.
- **The unbounded `_episode_steps_cache`/`_schema_cache` (Request 5.1 §7)
  remains a live concern** -- once per-shard/per-episode reads are cheap,
  an unbounded per-EpisodeRef cache stops making sense as a default and
  should likely become bounded/evictable in the same request that makes
  reads selective (Request 5.1 §10, unchanged recommendation).

## 25. Verification

- `make test` -- 1295 passed, 5 skipped (up from Request 5.1's 1289 --
  the new scale-fixture tests, §14)
- `make lint` -- all checks passed
- `make lerobot-test` -- 36 passed
- `make test-integration` -- 35 passed (real Postgres/MinIO via `make
  local-up`) -- confirms the legacy single-file reader/writer path
  (exercised by `scripts/e2e/e2e_fixture_bootstrap.py`, §23) is unaffected
  by any change in this request
- `make e2e-lerobot-container` -- **PASSED**: built the
  `lerobot-integration` image fresh, ran the full three-environment
  round-trip (persistent "interop" fixture -- still legacy single-file,
  §23 -- through the container -> real LeRobot v3 dataset -> official
  LeRobot reader -> golden semantic comparison), including its Step 4
  negative-path check (rerun against an already-populated target fails
  cleanly, target untouched). `exported_episode_count=3,
  exported_step_count=22, total_frames_readback=22` -- confirms the
  production job handler's changed write path (§17) doesn't regress this
  real container-boundary integration (this E2E's own fixture predates
  this request's writer switch, so it specifically exercises "does the
  changed reader still handle old-format manifests," not the new sharded
  write path directly -- see §26 for what *is* new-writer-path coverage).

## 26. Remaining limitations

- **`large` scale is 10,000 episodes but deliberately shallow (30
  steps/episode, 10 channels)**, not "10,000 episodes at `medium`'s
  depth" -- building the latter with today's unmodified, per-row Python
  `build_learning_steps_table`/`build_learning_signals_table` (Request
  2.5, explicitly out of this request's scope to rewrite) was
  impractical in this environment's time budget (§6's Request 5.1 finding
  that this builder scales roughly linearly in row count, confirmed again
  here: `large`'s build took ~83s at 300K/2.7M steps/signals rows).
  Whoever eventually needs true "10,000 episodes at realistic depth"
  numbers should expect proportionally longer builds until that writer is
  vectorized -- a pre-existing, not newly-introduced, limitation.
- **The residual PyArrow-vs-Polars writer size gap (§16) is unexplained**
  beyond "Polars likely applies additional encoding optimizations
  (possibly `BYTE_STREAM_SPLIT` for floats) that PyArrow's writer doesn't
  enable by default" -- a real follow-up investigation for whoever wants
  to shrink the measured storage overhead further, not resolved here.
- **`ShardPolicy.max_rows_per_shard` sizes against `learning_steps`' row
  count for both tables** (documented on `ShardPolicy` itself) --
  `learning_signals`' actual row count depends on channel-presence
  variation too (§14's ABSENT semantics), so a very wide or very sparse
  channel schema could produce a `learning_signals` shard noticeably
  larger or smaller than the row bound alone would suggest. Not observed
  as a problem at the scales/channel-counts measured here, but worth
  revisiting if a real export's channel width differs substantially from
  this benchmark's ~10-13 channels.
- **No real S3/MinIO latency was measured for this request either**
  (same gap Request 5.1 §13 flagged, still open) -- `LocalArtifactStore`
  only. The object-count reduction bounded-default provides (§15) should
  matter *more* under real network latency, not less, but that is an
  expectation, not a measurement.
- **Incremental/backfill shard-rebalancing is not implemented** (§15's
  "under-full last shard" is accepted as-is) -- a real backfill workflow
  that wants to keep shards close to `max_episodes_per_shard` over many
  incremental exports would need explicit shard-rebalancing logic this
  request does not provide.

---

# Request 5.3: Selective Artifact Access & Lazy Reads

Everything below is new; §1-26 above (Requests 5.1-5.2) are historical
record and unchanged. `SceneOpsDataset` remains the sole semantic access
boundary (§27's audit); no storage detail leaks into
`resolve_feature_schema`/`get_step`/`get_window`/`SequenceSampler`/
external adapters, all of which are unmodified call sites.

## 27. Minimum selective-read boundary (audit)

Traced path: `SceneOpsDataset` -> `ArtifactStore` -> Parquet bytes ->
Polars DataFrame -> episode/window reconstruction (Request 5.1 §1,
confirmed unchanged in shape). The exact boundary selective I/O needed to
cross, identified before writing any code:

- **`ArtifactStore.read_bytes` is whole-object only** (Request 5.1 §2) --
  the literal floor stopping anything selective. A new primitive was
  required; §28/§29.
- **Polars has no lazy/partial Parquet-over-arbitrary-bytes story that
  fits this repo's storage abstraction** -- `pl.scan_parquet` operates on
  paths/URLs its own I/O layer resolves directly, bypassing
  `ArtifactStore` entirely (backend-specific logic exactly where the task
  said not to put it). PyArrow, in contrast, accepts any Python file-like
  object satisfying `read`/`seek`/`tell` -- the natural fit for wrapping
  `ArtifactStore.read_range` without PyArrow (or Polars) ever knowing
  which backend is underneath. This is why PyArrow is the selective-read
  primitive here, not a Polars-vs-PyArrow preference in the abstract.
- **The reconstruction boundary was already the right shape** --
  `SceneOpsDataset._reconstruct_all_steps`/`get_step` were already the
  single choke point every step/window/schema-resolution call funnels
  through (Request 2.7B). Selective I/O only had to change *what feeds*
  that choke point (a targeted row-group fetch instead of a whole-table
  filter), never its signature or its callers.
- **The shard lookup index (§30) is the only new *stateful* thing
  `SceneOpsDataset` needed** -- built once at `open()`, from manifest
  metadata already in memory; everything downstream (schema cache,
  episode-steps cache) is the existing Request 2.7B/5.1 machinery,
  unmodified.

## 28. Final ArtifactStore selective-read interface

One new Protocol method, `sceneops_core.artifacts.contracts.ArtifactStore`:

```python
async def read_range(self, uri: ArtifactUri, offset: int, length: int) -> bytes:
    """Read exactly `length` bytes starting at byte `offset`."""
```

No seekable-file/random-access-object abstraction was added on top --
`read_range` alone turned out sufficient once the PyArrow side (§31) took
responsibility for deciding *how many* ranges to request and in what
order; `ArtifactStore` itself stays a simple, backend-agnostic
request/response contract, matching every other method on it.

Contract, identical on both backends: `offset >= 0`, `length > 0` are the
caller's responsibility; `ArtifactNotFoundError` if the artifact doesn't
exist; `ArtifactReadError` for a negative/zero-length request or a range
that exceeds the artifact's actual size (checked by comparing returned
byte count to requested length -- S3 does not error on an out-of-bounds
`Range` header by default, it silently clamps, so this check is required
for a *consistent* contract across backends, not optional on either).

## 29. Backend implementations

- **`LocalArtifactStore`** (`packages/sceneops-storage/sceneops_storage/
  backends/local.py`): `Path.open("rb")` + `seek(offset)` + `read(length)`.
- **`S3ArtifactStore`** (`.../backends/s3.py`): `get_object(Bucket=...,
  Key=..., Range=f"bytes={offset}-{offset+length-1}")`, run via the same
  `asyncio.to_thread` wrapper every other S3ArtifactStore method already
  uses. `InvalidRange`/`416` from a genuinely out-of-bounds request is
  caught and re-raised as `ArtifactReadError`, alongside the
  length-mismatch check above (MinIO/S3 don't always error the same way
  for an out-of-bounds range, so both paths are handled).
- **`CountingArtifactStore`** (test/benchmark-support,
  `sceneops_analytics.testing`) gained matching `read_range_calls`/
  `read_range_total`/`per_uri_range_*` counters, kept **separate** from
  the existing `read_bytes_*` counters -- a workload that reads
  selectively shows activity on one set, never the other, which is
  exactly what makes §34's measurements legible.
- Verified directly against both backends: `packages/sceneops-storage/
  tests/test_local_artifact_store.py` (new, 6 tests, no infra) and
  `packages/sceneops-storage/tests/test_s3_artifact_store.py` (+4 tests,
  real MinIO).

## 30. Shard lookup/index design

Built once, at `SceneOpsDataset.open()` (`_build_shard_lookup`,
`dataset.py`), directly from `LearningDataExportManifest.shard_index` --
**no Parquet file is opened, no object-store listing happens**, exactly
as required:

```python
dict[str, dict[EpisodeRef, tuple[LearningDataShard, ShardEpisodeMember]]]
#    ^table_name        ^EpisodeRef -> exactly which shard + row group
```

`None` for a v1 (legacy single-file) manifest -- there is nothing to look
up; `_get_steps_df`/`_get_signals_df` handle that layout directly and
unchanged (§32). `SceneOpsDataset._is_sharded` (`shard_lookup is not
None`) is computed once and dispatches every step/window read from then
on.

**Duplicate detection**: eager, at `open()` time -- if the same
`EpisodeRef` appears in more than one shard (or twice in one shard) for
one table, `ShardIndexMismatchError` is raised immediately while building
the lookup, before any read is attempted. Verified by
`test_duplicate_episode_ref_in_shard_index_raises_at_open`.

**Missing-mapping detection**: necessarily lazy -- an `EpisodeRef` this
dataset exposes (per `learning_episodes.parquet`) but that has no entry in
`shard_lookup[table_name]` is only observable once that specific
`EpisodeRef` is actually requested (`_fetch_episode_arrow_table` raises
`ShardIndexMismatchError` there). Verified by
`test_missing_episode_ref_in_shard_index_raises_on_access`.

## 31. PyArrow selective-read path

`sceneops_analytics/learning_dataset/parquet_range_reader.py` (new). The
mechanism, after one real implementation attempt that had to be replaced
(see the module's own header comment for the full story -- summarized
here):

**What was tried first and rejected**: manually parsing the Parquet
trailer (8-byte footer-length + magic) and footer to precompute exact
byte windows (footer region, target row group's exact byte span from
column-chunk offsets), then handing PyArrow a synchronous file-like object
that only ever served those two pre-fetched windows. This worked for
normally-sized files but **broke for small shards**: PyArrow's own reader
slurps small files whole rather than seeking (a reasonable internal
optimization), which asks for byte ranges this approach never
anticipated.

**What shipped**: a genuinely lazy, on-demand file-like object
(`_LazyRangeFile`) that fetches *whatever* range PyArrow asks for, via a
synchronous callback bridging into `ArtifactStore.read_range` through
`asyncio.run()` -- safe because the whole PyArrow interaction
(`_read_row_group_sync`) runs inside `asyncio.to_thread`, a plain worker
thread with no event loop of its own to conflict with. Every fetched
range is memoized in-memory (`_windows`); a later request fully contained
in an already-fetched window is served from memory, not re-fetched --
without this, small files (where the footer-area read and the target row
group's read can overlap heavily or fully contain each other) would fetch
the same bytes twice, which is exactly what was measured happening before
the fix (§34's MinIO test caught this: 50,981 bytes fetched for a shard
whose total size was 46,973 -- more than 100%, a real, since-fixed bug,
not a measurement artifact).

Resulting flow for one EpisodeRef, one table:

```text
shard_lookup[table_name][ref] -> (shard, member)
        -> read_episode_row_group(store, shard.uri, shard.size_bytes,
                                   member.row_group_index,
                                   cached_metadata=<from _shard_metadata_cache>)
        -> asyncio.to_thread(_read_row_group_sync, ...)
                -> pq.ParquetFile(_LazyRangeFile(...), metadata=cached_metadata)
                -> .read_row_group(member.row_group_index)
        -> pyarrow.Table -> .to_pylist() -> step_from_rows() (Request 2.7B, unchanged)
```

`shard.size_bytes` (already in the manifest, Request 5.2) means no
separate stat/HEAD call is ever needed to bound the virtual file. A
shard's parsed `FileMetaData` is cached per `SceneOpsDataset` instance,
keyed `(table_name, shard.shard_index)` -- the first episode read from a
shard pays a footer fetch; every subsequent episode from that same shard,
in the same dataset instance, does not (§34's "warm" measurements).

Content correctness verified directly against PyArrow's own full-file
`read_row_group()` (byte-identical `pa.Table.equals()`) during
development, and continuously by `test_v2_result_equivalent_to_legacy_v1_reader`
(§38) thereafter.

## 32. v1 fallback behavior

Completely unmodified: `_get_steps_df`/`_get_signals_df` still read
`table_uris["learning_steps"/"learning_signals"]` as one whole file each,
cached per `SceneOpsDataset` instance, filtered in memory per episode --
byte-for-byte the Request 2.7B/5.1 behavior. `_reconstruct_step` (the
narrow single-step filter) is untouched and still the v1 single-step path.

Still used by, unchanged:

- `interop_dataset.py` (the frozen correctness golden fixture).
- `scripts/e2e/e2e_fixture_bootstrap.py` (persistent "interop" E2E
  fixture backing `make e2e-lerobot`/`make e2e-lerobot-container`).
- Five test files that build fixtures inline via the legacy writer
  (`test_learning_dataset.py`, `test_sequence_sampler.py`,
  `test_torch_adapter.py`, `test_external_adapters.py`,
  `test_learning_consumer_adapter.py`) -- none test physical layout, so
  none were migrated (per this request's own instruction not to migrate
  golden fixtures solely for this request).

`get_step`/`_reconstruct_all_steps` each gained exactly one `if
self._is_sharded:` branch -- the v1 branch inside each is the pre-existing
code, moved, not rewritten.

## 33. Removal of v2 whole-table materialization

For a v2-sharded manifest, `_steps_df`/`_signals_df` are now **structurally
unreachable** -- `_get_steps_df`/`_get_signals_df` are called from exactly
one place each (`_reconstruct_all_steps`'s `else` branch), and that
branch only executes when `self._is_sharded` is `False`. There is no code
path left by which a v2 dataset could populate those fields; `_reconstruct_
all_steps_sharded`/`_fetch_episode_arrow_table` (the entire v2 path) never
reference them.

`_table_shard_uris`'s original 5.2-era "concatenate every shard into one
DataFrame" behavior (used for v2's `_get_steps_df`/`_get_signals_df`
before this request) was removed along with its caller -- the helper was
renamed to `_table_is_present` and simplified to a pure existence check
(still needed, unchanged in purpose, for `open()`'s missing-table
validation, which must still work correctly for both layouts).

## 34. Cold/warm EpisodeRef read measurements

`scripts/dev/benchmark_selective_reads.py` (new), against the production
shard policy (`default_shard_policy()`, 200 episodes/shard), at every
scale in the Request 5.2 ladder:

| scale | episodes | shards | total export bytes | A. open (bytes) | B. cold 1st episode (bytes) | C. warm 2nd episode, same shard (bytes) | D. cold episode, another shard (bytes) |
|---|---|---|---|---|---|---|---|
| tiny | 10 | 1 | 145,870 | 8,045 | 102,120 | 7,895 | n/a (1 shard) |
| small | 100 | 1 | 1,774,030 | 8,953 | 499,428 | 12,115 | n/a (1 shard) |
| medium | 1,000 | 6 | 11,808,009 | 10,016 | 783,581 | 8,869 | 782,465 |
| large | 10,000 | 51 | 94,306,183 | 23,127 | 775,756 | 5,928 | 775,092 |

Observations:

- **A (open)** stays metadata-only, as in Request 5.1/5.2 -- bytes here
  are `learning_episodes.parquet`'s own size, nothing from
  steps/signals shards.
- **B and D are consistently close to each other** (~775-800 KB at
  medium/large) -- both pay one footer fetch + one row-group fetch;
  *which* shard is cold makes no difference, only *whether* it's cold.
- **C (warm) is dramatically smaller than B/D at every scale** (7.9 KB /
  12.1 KB / 8.9 KB / 5.9 KB vs 100 KB-800 KB cold) -- confirms per-shard
  metadata caching works: a second episode in an already-touched shard
  pays only its own row-group fetch, no footer refetch.
- **tiny/small show only modest B/D reduction** (footer overhead
  dominates a single, whole-export-sized shard -- see §16's earlier
  footer-size-scales-with-row-group-count finding) -- this is expected and
  matches Request 5.2's own prediction, not a regression.

## 35. Actual bytes/object read amplification

Reduction factor (`old_baseline_bytes / new_bytes`, i.e. "how much smaller
is a single-episode fetch now"), same benchmark:

| scale | old baseline (100% of steps+signals) | v2 cold single-episode bytes | reduction factor |
|---|---|---|---|
| tiny | 145,870 | 102,120 | **1.4x** |
| small | 1,774,030 | 499,428 | **3.6x** |
| medium | 11,808,009 | 783,581 | **15.1x** |
| large | 94,306,183 | 775,756 | **121.6x** |

This is the direct, measured answer to Request 5.1's baseline finding
("one EpisodeRef access reads 100% of learning_steps/learning_signals"):
it no longer does, and the improvement **grows with scale** (more shards
per export -> a cold single-episode fetch touches a shrinking fraction of
the total) -- exactly the shape Request 5.2's shard-count-bounded design
was chosen to produce. `distinct_uris_read`/`per_uri_range_calls` (not
reproduced in the table, but part of `CountingArtifactStore`'s output)
confirm zero *other* shard's URI is ever touched for a single-episode
access -- proven directly, not inferred from byte counts alone, by
`test_v2_single_episode_access_touches_only_its_own_shard` and its MinIO
counterpart `test_v2_selective_window_access_works_against_real_minio`.

## 36. Fixed-window behavior

Measured directly (`E_fixed_window_vs_full_episode`, same benchmark): a
3-step window and the full episode window, on two different fresh
episodes at each scale, fetch **the same bytes** (within ~0.1-0.4%,
attributable to `length_jitter_fraction`'s per-episode row-count
variation, not to window width):

| scale | narrow window (horizon=3) bytes | full-episode bytes |
|---|---|---|
| tiny | 110,127 | 109,735 |
| small | 508,458 | 507,040 |
| medium | 793,673 | 793,140 |
| large | 798,816 | 798,812 |

This is the honestly-documented limitation the task explicitly asked for,
not a bug: one row group per episode (Request 5.2's chosen row-group
strategy) is the finest granularity this physical layout supports, so
`get_window(ref, start_step, horizon, ...)` always fetches the whole
episode's row group in both tables regardless of `start_step`/`horizon`,
then slices to the requested range **in memory**, after the fetch. Going
finer (true sub-episode byte-range pruning) would require Parquet page
indexes (`write_page_index=True`, not written by Request 5.2's writer) or
splitting an episode across multiple row groups (which would break the
"one row group per episode" invariant Request 5.2's manifest design
(`row_group_index` == list position) depends on) -- out of this request's
scope (`"do not repartition/rewrite the 5.2 physical layout"`), and
recorded here as input to whatever eventually revisits row-group
strategy.

## 37. FeatureProjection I/O implications

Confirmed unchanged after row-group selectivity, exactly as Request 5.1
§9 predicted it would be (`F_narrow_vs_wide_projection`, same benchmark):
a single-channel projection and the full projection, on two different
fresh episodes, fetch statistically the same bytes at every scale (largest
observed gap: 0.6%, well within per-episode row-count jitter):

| scale | narrow projection bytes | wide projection bytes |
|---|---|---|
| tiny | 109,457 | 110,185 |
| small | 508,138 | 509,223 |
| medium | 793,166 | 793,672 |
| large | 798,844 | 798,307 |

Root cause, reconfirmed at the row-group level: `learning_signals`' tall/
long schema (one row per `(step, channel)`, not one column per channel)
still has no per-channel *column* to project away -- narrowing
`FeatureProjection` only changes which channels the pure projection layer
(`sceneops_core.episodes.learning`, Request 2.7A) selects from an
already-fully-reconstructed `LearningStep`, never what PyArrow fetches or
decodes from the row group. This is recorded as a known, unresolved
physical-layout limitation (not something this request's scope permits
fixing -- doing so would mean redesigning `learning_signals`' logical
schema into a wide/columnar-per-channel shape, explicitly out of bounds:
`"do not redesign FeatureProjection"`/`"do not change the Phase 2 logical
schemas"`).

## 38. Logical-result equivalence

`test_v2_result_equivalent_to_legacy_v1_reader`: builds the *same*
entries (same `ScaleSpec`, same realistic variation) under both the
legacy v1 single-file layout and the new v2 sharded layout, opens both,
and asserts byte-identical `observation`/`action`/`timestamps_us` for
every window and every projected step, across every `EpisodeRef` the
fixture produces (including multi-revision episodes). Passing.

Also verified, all via the real v2 selective path (not mocked):

- **Window ordering/timestamps**
  (`test_window_ordering_and_timestamps_preserved_via_v2`): strictly
  increasing, distinct per step, matching the independent reference
  formula exactly.
- **Multiple revisions of the same `episode_id`**
  (`test_multiple_revisions_resolve_independently_via_v2`): two
  `EpisodeRef`s sharing `episode_id` but differing
  `aligned_artifact_checksum` resolve to distinct, independently-correct
  content through the shard lookup -- `EpisodeRef` revision identity
  (Request 2.7A §3) survives sharding and selective reads unchanged.
- **ABSENT vs MISSING** (`test_absent_and_missing_preserved_through_v2_round_trip`):
  core channels are always present and `RESOLVED`; extra channels are
  observed both genuinely ABSENT (omitted from the reconstructed
  `LearningStep.observations`/`.actions` dict) and genuinely `MISSING`
  (present, `status=MISSING`) after the full v2 write -> selective-read
  round trip -- the frozen Request 2.5A contract is unaffected by
  selective I/O.

## 39. Tests/integration results

New test files:

- `packages/sceneops-storage/tests/test_local_artifact_store.py` (new, 6
  tests) -- `read_range` correctness/error behavior, no infra.
- `packages/sceneops-storage/tests/test_s3_artifact_store.py` (+4 tests)
  -- same, against real MinIO.
- `packages/sceneops-analytics/tests/test_selective_reads.py` (new, 8
  tests) -- shard lookup, duplicate/missing detection, no-unrelated-shard
  selectivity, warm-metadata reuse, v1/v2 equivalence, multi-revision,
  window ordering, ABSENT/MISSING preservation (§30/§35/§38 above).
- `scripts/e2e/tests/test_selective_reads_minio_integration.py` (new, 1
  test) -- the same selectivity proof, against real MinIO, added to `make
  test-integration` (not `make test`, matching this repo's "real infra
  stays out of the fast tier by directory placement" convention -- see
  `makefiles/setup.mk`'s updated comment).

Verification commands, all green:

- `make test` -- **1303 passed, 5 skipped** (up from Request 5.2's 1295 --
  the 8 new `test_selective_reads.py` tests).
- `make lint` -- all checks passed.
- `make test-integration` -- **46 passed** (up from Request 5.2's 35 -- 6
  local `read_range` tests + 4 MinIO `read_range` tests + 1 MinIO
  selective-Parquet-read test).
- `make lerobot-test` -- 36 passed, unchanged.
- `make e2e-lerobot-container` -- **PASSED** (fresh image rebuild):
  `exported_episode_count=3, exported_step_count=22,
  total_frames_readback=22`. This exercises the (unchanged) v1 reader
  branch specifically, since the persistent "interop" E2E fixture stays
  on the legacy layout (§32) -- it proves this request's `SceneOpsDataset`
  changes did not regress the v1 path in a real container/adapter
  round-trip, not that the v2 path works (that is what §34-38's direct
  tests, built from the real, unmocked `write_sharded_learning_tables` and
  `SceneOpsDataset.open()`, already establish).
- **Not run**: a full `make e2e-episode-curation`-style production E2E
  exercising `EXPORT_LEARNING_DATA` through the real API/worker stack
  end-to-end. The local compose stack's `.env.local` was absent in this
  environment and Postgres/MinIO/API were reachable through some other
  already-running setup, making a full ingestion -> scene-building ->
  episode-building -> curation chain (this E2E's real prerequisite state)
  a nontrivial, environment-specific undertaking unrelated to this
  request's code changes. Given `test_export_learning_data_handler.py`
  already exercises the real (unmocked) `write_sharded_learning_tables`
  orchestration inside the real job handler, and §34-38's tests exercise
  the real (unmocked) reader against real Parquet files on both
  `LocalArtifactStore` and real MinIO, this was judged sufficient
  coverage of the actual code path change; flagged here rather than
  silently skipped.

## 40. Limitations handed to Request 5.4

- **Caching stayed intentionally minimal, as instructed.** The only new
  cache is `_shard_metadata_cache` (bounded by shards actually touched --
  at most ~50-100 entries at the scales measured, one `FileMetaData`
  object each, not row data). The pre-existing `_episode_steps_cache`
  (Request 2.7B, unbounded by EpisodeRef count) is unchanged and
  unaddressed -- Request 5.1 §7/§10 already flagged this as needing
  eviction once selective reads make re-fetching cheap; that need is now
  measurably real (§34/§35 show a cold fetch is cheap enough that
  "re-fetch instead of cache forever" is a genuinely viable alternative
  for high-cardinality access patterns), but implementing it is
  explicitly Request 5.4's job, not this one's.
- **Bulk/full-dataset access patterns were not optimized for.**
  `SequenceSampler.create()` still resolves every contributing episode's
  schema individually (Request 2.7C, unchanged) -- under v2, each of those
  now issues its own selective fetch rather than one shared whole-table
  load. For a workload that genuinely touches every episode anyway (a
  full training epoch, `SequenceSampler.create()` itself, a full-dataset
  export), this trades "one big sequential read" for "many small
  targeted reads" -- cheaper in *bytes* (§35) but not necessarily in
  *round trips*, especially over real network latency (§41's frozen
  unknown). A future optimization -- detect "this access pattern will
  touch every episode in a shard anyway, fall back to one bulk shard
  read" -- was considered and deliberately not implemented, as it borders
  on the caching/access-pattern-strategy territory this request was told
  to leave to 5.4.
- **`asyncio.run()`-per-read has real but currently-invisible overhead.**
  Every `_LazyRangeFile.read()` call spins up and tears down a fresh
  event loop (§31) -- fine at the read counts measured here (a handful of
  reads per row-group fetch), but this cost was not isolated/measured
  separately from network/disk I/O itself. If Request 5.4 (or a future
  request) needs to push selective-read throughput further, this is a
  candidate for a proper async-native PyArrow integration instead of the
  sync bridge used here.
- **Real S3/MinIO *latency* still hasn't been measured** (Request 5.1 §13,
  Request 5.2 §26, still open) -- §39's MinIO test proves correctness and
  selectivity, not that this request's read-count-per-episode (2-4 range
  reads) is *fast* over a real network vs. a local disk. Given
  selective reads meaningfully increase the *number* of round trips for
  bulk access patterns (previous point), this is now a more load-bearing
  unknown than it was in Request 5.1/5.2.

## 41. Frozen boundary for Request 5.4

Everything Request 5.1's §10/§13 already asked of "whoever comes next" is
now genuinely actionable, not just planned:

- `_episode_steps_cache`/`_schema_cache` bounded/evictable caching --
  the concrete data needed to size a sensible policy (per-episode cold-vs-
  warm cost, §34) now exists.
- Real S3/MinIO latency measurement, to weigh "many small selective
  reads" against "fewer large sequential reads" for bulk access patterns
  (§40) -- this is the one new question Request 5.3 raises that Request
  5.1/5.2 didn't have grounds to ask yet.
- Everything else frozen unchanged: `EpisodeRef` identity,
  `aligned_artifact_checksum` revision semantics,
  `learning_episodes`/`learning_steps`/`learning_signals` logical
  schemas, ABSENT vs MISSING, `FeatureProjection`/`FeatureSchema`,
  `SequenceSampler` window semantics, external adapter semantics,
  `Artifact`/lineage ownership -- none of this request's changes touch
  any of them (§38's equivalence tests are the proof, not just the
  claim).

---

# Request 5.4: Access Strategy & Bounded Cache

Everything below is new; §1-41 above (Requests 5.1-5.3) are historical
record and unchanged. The 5.2 shard/row-group layout and every frozen
Phase 2 logical semantic listed in §41 remain untouched. This request adds
bounded caching and a shard-aware bulk-access strategy strictly *on top
of* Request 5.3's selective-read mechanism -- no Parquet layout change, no
new physical files, no schema change.

## 42. Cache audit

Traced every in-memory cache `SceneOpsDataset` holds, before changing any
of them:

| cache | key | value | populated by | max cardinality (pre-5.4) |
|---|---|---|---|---|
| `_episode_steps_cache` | `EpisodeRef` | `list[LearningStep]` | `_reconstruct_all_steps` (get_step/get_window/resolve_feature_schema, both v1 and v2) | one entry per episode ever touched -- unbounded |
| `_schema_cache` | `(EpisodeRef, obs_channels, act_channels)` | `FeatureSchema` | `resolve_feature_schema` | one entry per (episode, projection) ever resolved -- unbounded |
| `_shard_metadata_cache` | `(table_name, shard_index)` | `pq.FileMetaData` | `_fetch_episode_arrow_table`/`_bulk_fetch_shard_steps` (v2 only) | one entry per (table, shard) ever touched -- bounded by shard count, which grows far slower than episode count under the Request 5.2 shard policy |
| `_steps_df`/`_signals_df` | n/a (singleton) | whole-table `pl.DataFrame` | `_get_steps_df`/`_get_signals_df` (v1 only) | exactly 2 entries, but each holds the *entire* export's table -- pre-existing, unbounded-by-design v1 behavior, untouched by this request (§48) |

Measured per-entry memory footprint (`tracemalloc`, touching 50 episodes
at each scale, production shard policy):

| scale | episodes touched | traced bytes | bytes/episode |
|---|---|---|---|
| tiny | 13 (all) | 17,140,085 | 1,318,468 |
| small | 50 | 137,106,381 | 2,742,128 |
| medium | 50 | 83,210,708 | 1,664,214 |
| large | 50 | 33,437,871 | 668,757 |

`_episode_steps_cache` is, by a wide margin, the dominant cost --
0.6-2.7 MB per cached episode, scaling with `steps_per_episode x
channel_count` (small's episodes are both longer and wider than large's,
hence the higher per-episode cost despite "large" being the bigger
dataset overall). `_schema_cache`'s own standalone footprint is small
(dimension/kind metadata only, no step data -- isolating it precisely is
hard since resolving a schema always reconstructs steps first as a
side-effect, but the schema object itself is nowhere near
megabyte-scale). `_shard_metadata_cache` measured ~8 KB/entry (16,485
bytes for 2 entries after touching 50 episodes spanning parts of 2
shards) -- confirmed small and shard-count-bounded, not episode-count-bounded.

**Recomputation cost if evicted**: cheap for all three, given Request
5.3's selective reads -- an evicted `_episode_steps_cache`/`_schema_cache`
entry re-fetches via one shard's row-group read (not the whole table);
an evicted `_shard_metadata_cache` entry re-fetches via one footer read.
This is exactly why bounding these caches is safe: eviction costs a
bounded re-fetch, never a correctness problem, and never a full-table
reload (Request 5.1's original bottleneck).

**Extrapolated unbounded-growth risk**: at `large` scale (10,000
episodes, ~0.67 MB/episode measured), an unbounded `_episode_steps_cache`
that ends up touching every episode once (a plausible outcome of one full
training epoch) would hold on the order of 6.7 GB -- for a dataset whose
total Parquet footprint is under 100 MB (§18/§35). This is the concrete
number motivating §43's bound.

## 43. Files changed

**sceneops-analytics** (bounded caches, bulk access, cheaper bridge):

- `sceneops_analytics/learning_dataset/lru_cache.py` (new) --
  `BoundedCache` (plain `OrderedDict`-backed LRU, `max_size=None`
  unbounded / `0` disabled) + `CacheStats` (hits/misses/evictions,
  instrumentation only).
- `sceneops_analytics/learning_dataset/cache_policy.py` (new) --
  `CachePolicy` (`max_episode_steps`/`max_schemas`/`max_shard_metadata`),
  `DEFAULT_CACHE_POLICY`, `DISABLED_CACHE_POLICY`.
- `sceneops_analytics/learning_dataset/dataset.py` -- `SceneOpsDataset.
  open()`/`__init__` gain `cache_policy: CachePolicy | None = None`; the
  three v2-relevant caches are now `BoundedCache` instances instead of
  plain dicts (`_steps_df`/`_signals_df`, v1-only, unchanged). New
  methods: `group_by_shard()`, `preload_episodes()`,
  `resolve_feature_schemas_bulk()`, `_bulk_fetch_shard_steps()`. Step
  reconstruction logic factored into a shared pure helper
  (`_build_steps_from_tables`) reused by both the single-episode and
  bulk paths.
- `sceneops_analytics/learning_dataset/parquet_range_reader.py` --
  new `read_shard_row_groups_bulk()` (+ `_row_group_byte_span()`, +
  `_read_row_groups_bulk_sync()`) for combined multi-row-group reads.
  The `asyncio.run()`-per-range bridge (`_sync_read_range`) replaced with
  `run_coroutine_threadsafe` against the caller's own already-running
  event loop (§46).
- `sceneops_analytics/learning_dataset/sampler.py` -- `SequenceSampler.
  create()` now batches schema resolution via
  `dataset.resolve_feature_schemas_bulk()` instead of one independent
  `resolve_feature_schema()` call per contributing Episode.
- `sceneops_analytics/learning_dataset/errors.py` -- no new errors;
  `ShardIndexMismatchError` (Request 5.3) reused for bulk-path missing
  mappings.
- `sceneops_analytics/__init__.py`/`learning_dataset/__init__.py` -- new
  exports (`CachePolicy`, `DEFAULT_CACHE_POLICY`, `DISABLED_CACHE_POLICY`).

**Tests** (31 new, all passing):

- `packages/sceneops-analytics/tests/test_lru_cache.py` (new, 7 tests) --
  `BoundedCache` unit coverage.
- `packages/sceneops-analytics/tests/test_cache_and_bulk_access.py` (new,
  12 tests) -- `CachePolicy` wiring, `group_by_shard`/`preload_episodes`/
  `resolve_feature_schemas_bulk` correctness and selectivity (via
  `CountingArtifactStore`), `SequenceSampler.create()`'s bulk path (never
  calls `get_window` during construction, produces a consistent schema
  across shards, never touches a too-short episode), v1 compatibility.

**Benchmarks** (new, non-production):

- `scripts/dev/benchmark_cache_and_bulk_access.py` -- local scale-ladder
  benchmark (§44).
- `scripts/dev/benchmark_minio_access_strategy.py` -- real-MinIO A-E
  scenario benchmark (§45).

## 44. Bounded-cache policy and defaults

`CachePolicy(max_episode_steps: int | None, max_schemas: int | None,
max_shard_metadata: int | None)` -- a plain, frozen dataclass, passed
(optionally) to `SceneOpsDataset.open()`. `None` means unbounded (the
pre-Request-5.4 default, still available for a caller who knows its own
working set is small); `0` disables that cache entirely.

```python
DEFAULT_CACHE_POLICY  = CachePolicy(max_episode_steps=64,  max_schemas=128, max_shard_metadata=256)
DISABLED_CACHE_POLICY = CachePolicy(max_episode_steps=0,   max_schemas=0,   max_shard_metadata=0)
```

Policy chosen (simple LRU, per the task's own preference for "the
simplest policy the evidence supports" -- §42's measurements didn't
surface a reason to weight entries by size, add TTLs, or add
generations): `max_episode_steps=64` is deliberately small relative to
realistic dataset sizes, since it is the dominant cost (§42); `max_
schemas=128` and `max_shard_metadata=256` are looser, reflecting their
measured near-negligible per-entry footprint. `CachePolicy` is
per-`SceneOpsDataset`-instance, never a module-level/process-global
cache, and is purely a performance knob -- it is never consulted by, or
threaded into, `sceneops_core.episodes.learning`'s pure projection
functions, so it cannot affect any method's *result*, only what gets
re-fetched vs reused (verified directly: §49's equivalence tests compare
bulk-path vs individual-path results, and cache-disabled vs
cache-enabled results, byte-for-byte).

**A real bug found and fixed during this request's own development**:
the first implementation had `preload_episodes()` bulk-insert an entire
shard's episodes into `_episode_steps_cache` *before* any consumer read
them back out. With the default policy's `max_episode_steps=64` smaller
than the Request 5.2 shard policy's `max_episodes_per_shard=200`, this
caused most of a just-bulk-fetched shard to be LRU-evicted by the tail
end of its own insertion -- before `SequenceSampler.create()`'s
schema-resolution loop ever got to read most of it back, forcing a
silent fallback to individual re-fetches and defeating the bulk
optimization almost entirely (measured: 2,035 range calls at `medium`
scale, indistinguishable from the pre-bulk baseline). Fixed by
introducing `resolve_feature_schemas_bulk()`, which returns its bulk
fetch's results directly to the caller rather than routing them through
the bounded cache and hoping they survive (§46/§49) -- the cache is now
purely an *opportunistic* side effect of the bulk fetch, never something
correctness or the main efficiency win depends on.

## 45. Selective vs bulk access strategy

No automatic heuristic was added -- per the task's own preference
("expose separate internal bulk primitives and let known bulk callers
use them" over a generic optimizer). Three explicit primitives, all on
`SceneOpsDataset`:

- **`group_by_shard(refs)`** -- chunks a ref list into shard-contiguous
  groups, preserving order (a no-op reordering for v1; always one group
  for v1 since there is no shard concept). Pure, no I/O -- relies on
  `dataset.episodes()`'s sorted order already being shard-contiguous by
  construction (Request 5.2's shard assignment is built from that exact
  sorted sequence).
- **`preload_episodes(refs)`** -- cache-priming only: groups by shard,
  bulk-fetches each group, opportunistically populates
  `_episode_steps_cache`. No guarantee an entry survives until later use
  (§44's bug fix applies) -- intended for callers that just want to warm
  the cache ahead of an access pattern they don't fully control the
  ordering of.
- **`resolve_feature_schemas_bulk(refs, projection)`** -- the primitive
  `SequenceSampler.create()` actually uses: groups by shard, bulk-fetches,
  and returns every ref's schema directly from that fetch's own result
  (never depending on the cache). Each ref's schema is still resolved
  fully independently; this only batches the underlying I/O, never
  shares or assumes compatibility across episodes.

Sparse (few, scattered `EpisodeRef`s) access continues to use the
Request 5.3 selective per-episode path unchanged -- calling
`get_window()`/`get_step()` directly, with no bulk primitive involved,
remains correct and reasonably efficient for that pattern (§46's A/B/E
measurements). Dense/bulk access (most/all of a shard, or of the whole
dataset -- `SequenceSampler.create()`, and any future caller with the
same shape of access pattern) should call one of the two bulk primitives
above explicitly.

## 46. SequenceSampler.create() changes

Before (Request 5.3, unchanged since): one independent
`resolve_feature_schema()` call per contributing Episode, in
`dataset.episodes()` order -- for v2, each call is its own selective
shard+row-group fetch, so total I/O calls scale with **episode count**.

After: contributing Episodes are grouped by shard
(`dataset.group_by_shard`) and resolved via one
`resolve_feature_schemas_bulk()` call over the *entire* contributing set
-- internally, this still fetches per shard group, so total I/O calls
now scale with **shard count**. Each Episode's schema is still resolved
fully independently and the cross-episode consistency check (raising
`SamplerSchemaMismatchError` at the first disagreement) is unchanged in
logic, only in what precedes it.

Measured (`medium` scale, 1,000 episodes / 6 shards):

| | range calls | wall seconds |
|---|---|---|
| before (Request 5.3, per-episode) | 2,035 | 21.582 |
| after (Request 5.4, per-shard bulk) | 35 | 9.117 |

At `large` scale (10,000 episodes / 51 shards): 305 range calls, 105.5s
wall time for 56,068 windows -- I/O calls track shard count (roughly 6
per shard x 51 shards), confirming the scaling property holds at 10x the
episode count. The remaining ~100s of wall time is not I/O -- it is pure
Python object reconstruction (building 10,000 episodes' `list[LearningStep]`
graphs), the same Request 5.1-identified cost this request does not
address (§50).

**One honestly-disclosed, minor behavioral nuance**: the old code
checked `SamplerSchemaMismatchError` progressively, episode-by-episode,
in strict `dataset.episodes()` order, so if *multiple* episodes had
independent problems (one a schema mismatch, a different one, in a
different shard, a `FeatureAbsentError`), the earliest-in-order failure
always won. The new code resolves an entire shard group's schemas before
running any consistency check, so a `FeatureAbsentError` from a
later-shard episode could now surface before a `SamplerSchemaMismatchError`
from an earlier-shard episode, reversing which specific error is
reported first in that rare multi-simultaneous-failure case.
`SequenceSampler.create()` still fails either way -- only which
diagnostic is reported first, in an edge case with no existing test
coverage either direction, changes. Not treated as a "sampling/window
semantics" change (window count/ordering/stride math, and the single-
failure-mode consistency check itself, are unaffected and covered by
existing + new tests), but disclosed here rather than silently accepted.

## 47. Local benchmark results

`scripts/dev/benchmark_cache_and_bulk_access.py`, production shard
policy, tiny/small/medium measured together, `large` measured separately
(see §50 for why):

| scale | A disabled (bytes/calls) | B bounded (bytes/calls) | C1 repeat disabled (2nd pass bytes) | C2 repeat bounded (2nd pass bytes) | D1 full-iter disabled (bytes, N ep) | D2 full-iter bounded (bytes, N ep) |
|---|---|---|---|---|---|---|
| tiny | 2,014,156 / 56 | 171,552 / 21 | 2,014,156 | 0 | 2,586,832 (13) | 195,494 (13) |
| small | 20,022,028 / 240 | 750,604 / 44 | 10,012,628 | 0 | 110,061,176 (110) | 1,905,078 (110) |
| medium | 31,326,182 / 240 | 4,038,616 / 60 | 15,660,996 | 0 | 782,895,634 (500) | 6,332,034 (500) |
| large | 31,037,174 / 240 | 15,518,587 / 120 | 15,506,570 | 0 | 775,107,422 (500) | 4,981,398 (500) |

Notes:

- **A vs B** (20 spread-out episodes, cache disabled vs bounded): bounded
  caching alone (without any bulk fetch involved -- these are 20
  individual `get_window` calls either way) already cuts bytes 12-2x
  across scales, purely from `_shard_metadata_cache` avoiding repeat
  footer fetches when 2+ of the 20 sampled episodes land in the same
  shard.
- **C1 vs C2** (10-episode working set, touched twice): disabled cache
  re-fetches the *entire* second pass identically to the first (proving
  zero reuse, as designed); bounded cache's second pass is **zero bytes,
  zero calls** at every scale -- a clean cache hit.
- **D1 vs D2** (full/high-density iteration, capped at 500 episodes for
  runtime): disabled cache costs 100-150x more bytes than bounded at
  `medium`/`large` -- each of the 500 episodes pays a full footer
  refetch with zero reuse under `DISABLED_CACHE_POLICY`, which is by
  design (it exists for exactly this kind of raw-cost measurement, not
  as a realistic production setting).
- `E_sequence_sampler_create` results are §46's table, reproduced from
  the same benchmark run.

## 48. Real MinIO latency/request results

`scripts/dev/benchmark_minio_access_strategy.py`, 60 episodes / 6 shards
(10 episodes/shard), against a real local MinIO instance:

| scenario | episodes | wall seconds | bytes | range calls |
|---|---|---|---|---|
| A. cold selective EpisodeRef | 1 | 0.0081 | 81,357 | 2 |
| B. warm selective EpisodeRef (same ref) | 1 | 0.0002 | 0 | 0 |
| C. same shard, individual reads | 10 | 0.0703 | 119,770 | 20 |
| D. same shard, bulk access (`preload_episodes`) | 10 | 0.0202 | 79,846 | 2 |
| E. spread across shards, individual reads | 6 (1/shard) | 0.0504 | 480,601 | 12 |

**C vs D is the headline result**: the identical 10-episode access,
bulk-primed, is **3.5x faster** (70.3ms -> 20.2ms) and issues **10x
fewer range requests** (20 -> 2) over a real network. D's cache
hits/misses (`10 misses, 20 hits` counted internally across the preload
+ consumption) confirm every one of the 10 episodes was served from the
bulk-primed cache, not re-fetched individually. E confirms cross-shard
access pays its own cold cost per shard (2 calls x 6 shards = 12,
matching), with no cross-shard interference.

## 49. Memory-growth comparison

Directly from §47's A/B rows plus `traced_peak_bytes` (not tabulated
above for space -- see the script's own `--out` JSON for full figures):
bounded caching's `traced_peak_bytes` is consistently *higher* than
disabled caching's for a single sparse pass (since bounded caching, by
design, keeps entries around after the pass instead of letting them be
garbage-collected immediately) -- this is expected and correct: the
whole point of caching is trading memory for avoided re-fetch cost.
`episode_steps_cache_len` after each pass never exceeds
`CachePolicy.max_episode_steps` (64) regardless of how many episodes
were actually touched (13/110/1000+/10000+ across scales) -- direct,
measured confirmation the bound holds under real access patterns, not
just in the unit tests.

## 50. asyncio bridge measurement and change

Isolated the `_LazyRangeFile` bridge's per-call overhead from real I/O
latency, before and after the fix (§45's `_sync_read_range`):

| backend | old (`asyncio.run()` per call) | new (`run_coroutine_threadsafe`) |
|---|---|---|
| local disk | ~196 us/call overhead (21us baseline -> 217us) | ~222 us/call *in this isolated microbenchmark* (dominated by `asyncio.to_thread`'s own per-call cost, not the bridge mechanism -- see caveat below) |
| real MinIO | ~1,387 us/call overhead (+58% on a ~2.4ms round trip) | ~285 us/call overhead (+11.3% on a ~2.8ms round trip) |

**Judged material and fixed** (the real-MinIO case, +58%, is squarely
what the task calls out as needing a cleaner bridge): the old
`asyncio.run()` approach created a brand-new event loop *and* a
brand-new default thread-pool executor on every single range read, and
`S3ArtifactStore.read_range`'s own internal `asyncio.to_thread` then
nested a *second* executor inside that -- compounding overhead was the
root cause. Fixed by capturing the caller's already-running event loop
once (`asyncio.get_running_loop()`, before entering the worker thread)
and using `asyncio.run_coroutine_threadsafe(coro, loop)` to schedule
each range read back onto that existing loop -- no new loop, no new
executor, per range read.

**Caveat on the local-disk row**: the isolated microbenchmark above
calls `asyncio.to_thread` once *per raw range read* to measure the
bridge in isolation, which is harsher than the real call pattern --
`parquet_range_reader.py` only pays `asyncio.to_thread`'s own dispatch
cost **once per row-group fetch** (2-4 raw range reads happen *inside*
that one thread dispatch, each only paying the much cheaper
`run_coroutine_threadsafe` marginal cost). ArtifactStore was
deliberately not redesigned into a synchronous API to avoid this
overhead entirely, per the task's own constraint.

## 51. v1 regression status

No v1 code path touched: `_get_steps_df`/`_get_signals_df`/
`_reconstruct_step` are byte-for-byte unchanged from Request 5.3.
`CachePolicy`/`preload_episodes`/`resolve_feature_schemas_bulk` all
detect `self._is_sharded is False` and either no-op
(`preload_episodes`) or fall back to the existing per-episode
`resolve_feature_schema` path (`resolve_feature_schemas_bulk`) --
verified by `test_group_by_shard_v1_returns_single_group` (using the
frozen `interop_dataset.py` golden fixture directly) and by the full
existing v1 test suite passing unmodified. `_steps_df`/`_signals_df`
remain the only genuinely unbounded caches in the codebase after this
request -- an accepted, documented (§42) exception, not an oversight:
v1 is the frozen correctness-fixture-only path, not the production
optimization target.

## 52. Logical-semantic equivalence

- `test_preload_episodes_bulk_fetch_matches_individual_fetch` --
  bulk-preloaded-then-read windows are identical (`observation`/`action`)
  to windows read via the individual selective path, per episode.
- `test_resolve_feature_schemas_bulk_matches_individual_resolution` --
  every ref's bulk-resolved `FeatureSchema` equals its individually-
  resolved counterpart.
- `test_resolve_feature_schemas_bulk_does_not_depend_on_cache_survival`
  -- with a cache bound smaller than the shard being bulk-fetched (the
  exact condition that exposed §44's bug), results are still fully
  correct for every ref.
- `test_sampler_create_bulk_path_produces_consistent_schema_across_shards`,
  `test_short_episode_never_bulk_fetched`, `test_sampler_create_never_
  calls_get_window_v2` -- `SequenceSampler`'s frozen invariants (one
  compatible schema, zero-window episodes never touched, construction
  never materializes a window) hold under the new bulk path exactly as
  under the old per-episode path.
- Every Request 2.7A/2.7B/5.1-5.3 test (window ordering, ABSENT/MISSING,
  multi-revision `EpisodeRef`s, `SamplerSchemaMismatchError`) continues
  to pass unmodified against both v1 and v2.

## 53. Tests/integration results

- `make test` -- **1,322 passed, 5 skipped** (up from Request 5.3's
  1,303 -- 19 new tests: 7 `BoundedCache` unit tests + 12
  cache/bulk-access tests).
- `make lint` -- all checks passed.
- `make test-integration` -- 46 passed, unchanged from Request 5.3 (no
  new integration tests added this request -- the MinIO A-E benchmark,
  §48, is a manually-run script, not a pytest integration test).
- `make lerobot-test` -- 36 passed, unchanged.
- `make e2e-lerobot-container` -- **PASSED** (fresh image rebuild):
  `exported_episode_count=3, exported_step_count=22,
  total_frames_readback=22` -- confirms the bounded-cache/bulk-access
  changes don't regress the real container/adapter round-trip (this
  fixture stays on the v1 path, §51, so it specifically re-confirms v1
  is untouched, complementing §52's direct v2 equivalence tests).

## 54. Remaining limitations

- **`read_shard_row_groups_bulk`'s combined-span pre-fetch is a
  min-to-max byte range across every requested row group** -- efficient
  when the requested subset is dense/contiguous within the shard (every
  caller in this request -- `SequenceSampler.create()`'s "most/all
  contributing episodes" -- satisfies this), but would over-fetch a lot
  of unneeded bytes if a future caller requested a *sparse* subset of one
  shard's row groups (e.g. only the first and last of 200). Not
  encountered or measured here; worth flagging for whoever adds the next
  bulk caller.
- **The error-precedence nuance in §46** (which of several simultaneous
  failures across shards is reported first) is disclosed, not fixed --
  low risk (an edge case with no existing test coverage either
  direction) but a real, if minor, behavioral difference from Request
  5.3.
- **`CachePolicy`'s defaults (64/128/256) are evidence-based but not
  exhaustively tuned** -- derived from this benchmark's channel
  counts/episode lengths (§42); a real workload with very different
  episode sizes should re-measure and override, not assume these
  defaults are universally correct.
- **The asyncio bridge's remaining ~220-285us/call overhead was reduced
  (~5x against real MinIO) but not eliminated** (§50) -- a fully
  async-native PyArrow integration could remove it further; out of this
  request's scope (`"do not redesign ArtifactStore into a synchronous
  API"` was the only hard constraint here, and is respected).
- **`large`-scale local benchmark numbers (§47) were collected in a
  separate run from tiny/small/medium**, not one combined pass -- purely
  a practicality accommodation in this environment (the combined run
  exceeded a 5-minute wall-clock budget), not a methodology difference;
  the same script, same code, same measurement logic produced both.
- **Real MinIO latency was characterized against one local instance
  only** (§48, same caveat as Request 5.1 §13/Request 5.3 §41) -- no
  measurement across real network conditions (different regions, cold
  TCP connections, concurrent load) exists yet.
- **Incremental/backfill export remains unimplemented** (explicitly out
  of this request's scope, unchanged from Request 5.2/5.3).

## 55. Frozen boundary for Request 5.5

- `CachePolicy`'s three bounds, `BoundedCache`'s LRU-only eviction
  policy, `group_by_shard`/`preload_episodes`/`resolve_feature_schemas_bulk`
  as the only bulk primitives, and the `run_coroutine_threadsafe` bridge
  are all now in place and measured -- Request 5.5 (or later) inherits a
  working, bounded, bulk-aware baseline rather than an unbounded one.
- Explicitly **not** attempted here, and still open: a generic
  access-pattern-detecting heuristic (deliberately not built, per this
  request's own constraint), size-weighted/TTL cache eviction beyond
  simple LRU (nothing in §42's measurements justified the added
  complexity), incremental/backfill export, and full async-native
  PyArrow I/O (§54).
- Everything else frozen unchanged, transitively from Request 5.1-5.3:
  `EpisodeRef`/revision identity, `learning_episodes`/`learning_steps`/
  `learning_signals` logical schemas, ABSENT vs MISSING,
  `FeatureProjection`/`FeatureSchema` semantics, `SequenceSampler` window
  semantics, external adapter semantics, the Request 5.2 shard/row-group
  physical layout, `Artifact`/lineage ownership -- none of this
  request's changes touch any of them (§52's equivalence tests are the
  proof).

---

# Request 5.5: Incremental Learning Data Export

Everything below is new; §1-55 above (Requests 5.1-5.4) are historical
record and unchanged. This request adds a second export path --
incremental, deriving a new immutable export from a prior one by reusing
unchanged physical shards -- strictly *alongside* the existing full-export
path. No change to the Request 5.2 shard/row-group layout, no change to
`SceneOpsDataset`, no new scheduler, no deletion/tombstone/compaction
support.

## 56. Current export lifecycle (audit)

Before this request, `EXPORT_LEARNING_DATA` (`export_learning_data.py`)
had exactly one mode: resolve/verify/validate every pinned input, compute
`export_id` as a pure content hash of the sorted aligned checksums plus
`export_config`/`schema_version` (`learning_data_export_id`, Request 2.5),
then build and write every requested table from scratch --
`learning_episodes` as one file, `learning_steps`/`learning_signals` via
`write_sharded_learning_tables`, which calls `plan_shards_for_entries` to
**re-bin every input from scratch** on every call (sorts the full entry
set by `(episode_id, aligned_artifact_checksum)`, then bin-packs under
`ShardPolicy`) -- so even a single new EpisodeRef added to an existing
1000-episode export would, before this request, rewrite effectively all
of that export's shards, one ArtifactRecord per shard, one manifest, one
`context.commit()` as the sole atomicity boundary. This full-rebuild-only
behavior is exactly what made incremental addition worth building:
`plan_shards_for_entries`'s own re-binning is deterministic given the
same entry *set*, but any set change shifts bin boundaries downstream of
the change, so nothing was reusable without new machinery.

## 57. Chosen incremental semantics

An incremental export (`base_export_id` set) is **pure addition only**:
its final exposed EpisodeRef set must be a superset of `base_export_id`'s
own set -- never a replacement or removal. Concretely:

- **Append new EpisodeRefs** -- new `episode_id`s land in freshly-binned
  shards; the base's own shards are untouched.
- **New revision of an existing `episode_id`** -- since `EpisodeRef`
  identity is always `(episode_id, aligned_artifact_checksum)`, never
  `episode_id` alone, a second aligned revision of an already-exported
  episode is just another new key -- both revisions coexist as distinct,
  independently-addressable `EpisodeRef`s in the resulting export. No
  special-casing was needed for this in the planner; it falls out of
  treating identity as the full pair (verified directly by
  `test_plan_supports_new_revision_of_existing_episode_id` and
  `test_incremental_export_new_revision_of_existing_episode`).
- **Never dedup by `episode_id` alone** -- confirmed at every layer: the
  planner's key sets, the job handler's revision merge, and the manifest's
  `inputs` list all key on the full pair.
- The job handler's own params contract makes the "no removal" guarantee
  structural, not just enforced by a check: `ExportLearningDataJobParams.
  inputs` for an incremental job is **only the delta**, and the handler
  always computes `all_revisions = base_manifest.inputs + inputs` --
  there is no field a caller can set to make a base EpisodeRef disappear
  from the merged target set. `plan_incremental_export`'s own
  base-subset-of-target check (§59) is therefore unreachable from this
  job's params in practice; it remains as defense-in-depth for the pure
  function's other, hypothetical callers, and is exercised directly by
  `packages/sceneops-core/tests/test_incremental_export_planner.py`
  rather than through the job handler.

## 58. Selected shard-reuse strategy

Two candidates were considered:

| | tail-shard rewrite | **append-only delta shards (chosen)** |
|---|---|---|
| base shards touched | the (typically under-full) last shard is rewritten to top it off | **none, ever** |
| new ArtifactRecords | 1 replacement record for the rewritten tail shard + N for new shards | **only N for genuinely new shards** |
| bytes rewritten | O(1 shard) extra, regardless of delta size | **zero** |
| determinism | requires deciding *which* shard is "the tail" and whether to re-open it, adding a stateful edge case | pure set diff -- base's shards are always 100% reused under the pure-addition precondition (§57), no partial-shard-membership logic needed at all |
| long-run shard-size distribution | stays closer to `ShardPolicy`'s target size over many increments | many small increments leave many small trailing shards -- accepted, deferred to a future, separate compaction operation (§66) |

Append-only delta shards won on simplicity and determinism, at the
explicitly accepted cost of the long-run shard-size distribution -- this
request's own non-goals rule out building compaction to offset that cost
(§66). A key simplifying consequence of the pure-addition precondition
(§57): once `base_manifest`'s own EpisodeRef set is confirmed to be a
subset of the target set, **all** of the base's shards are always fully
reusable -- there is no per-shard partial-membership case to detect or
handle, because nothing about an existing shard's membership can ever
become invalid under addition-only semantics.

## 59. Planner design

`packages/sceneops-core/sceneops_core/episodes/learning_export/
incremental.py` -- pure, no DB/ArtifactStore/Parquet access:

```
plan_incremental_export(base_manifest, target_revisions) -> IncrementalExportPlan
```

- Validates `base_manifest.shard_index is not None` (a v1 single-file
  base has nothing physical to reuse) --
  `IncrementalExportUnsupportedBaseError` otherwise.
- Validates `target_revisions` has no duplicate `(episode_id,
  aligned_artifact_checksum)` keys -- `IncrementalExportOverlapError`
  otherwise (this is what actually fires from the job handler when a
  caller's delta accidentally redeclares an already-included base
  revision, §57's last bullet).
- Validates `base_manifest.inputs`' key set is a subset of
  `target_revisions`' key set -- `IncrementalExportUnsupportedBaseError`
  otherwise, directing the caller to a full export.
- Returns `IncrementalExportPlan`: `base_export_id`,
  `reused_learning_steps_shards`/`reused_learning_signals_shards` (the
  base's own `LearningDataShard` objects, verbatim -- same
  `uri`/`checksum`/`size_bytes`), `reused_episode_refs`/
  `new_episode_refs` (both sorted, deterministic), and
  `start_shard_index` (`len(base's own shard list)`, so new shards can
  never collide with a reused one's index/URI).

**Deliberately does not take `ShardPolicy` as a parameter**, despite the
task's own conceptual formula (base manifest + target EpisodeRefs +
ShardPolicy -> plan) naming it as an input: under the pure-addition
precondition, reuse decisions are pure set-membership diffs, entirely
independent of any bin-packing policy -- policy only matters when
actually binning the *new* episodes into shards, which requires each
new episode's real row count (from its parsed `AlignedEpisodeArtifact`,
not available at this identity-only planning layer). That binning step
lives in the execution layer instead
(`write_incremental_sharded_learning_tables` in
`sceneops_analytics/learning_tables_sharded.py`), which reuses the
existing, unmodified `plan_episode_shards`/`plan_shards_for_entries` --
only now called over the delta alone, numbered from
`plan.start_shard_index`, then the plan's reused shards are prepended
verbatim. `write_sharded_learning_tables` itself gained one new
optional parameter, `start_shard_index: int = 0`, to support this
without any change to its existing (full-export) call sites' behavior.

Determinism, reuse correctness, "no removal" rejection, duplicate-key
rejection, and the new-revision-of-existing-episode_id case are all
covered directly and pass:
`packages/sceneops-core/tests/test_incremental_export_planner.py`
(7 tests, no I/O).

## 60. Files changed

- **New**: `packages/sceneops-core/sceneops_core/episodes/learning_export/
  incremental.py` (`IncrementalExportPlan`, `plan_incremental_export`,
  `IncrementalExportUnsupportedBaseError`, `IncrementalExportOverlapError`).
- **New**: `packages/sceneops-core/tests/test_incremental_export_planner.py`,
  `apps/worker/tests/jobs/test_export_learning_data_incremental.py`,
  `scripts/dev/benchmark_incremental_export.py`.
- `packages/sceneops-core/sceneops_core/episodes/learning_export/
  schemas.py` -- additive `LearningDataExportManifest.base_export_id:
  str | None = None` (lineage/traceability only; readers never need it
  to interpret `table_uris`/`shard_index`, which are always already
  complete and self-describing).
- `packages/sceneops-core/sceneops_core/episodes/learning_export/
  __init__.py` -- exports the four new symbols.
- `packages/sceneops-core/sceneops_core/jobs/schemas/params/episodes.py`
  -- additive `ExportLearningDataJobParams.base_export_id: str | None =
  None`.
- `packages/sceneops-core/sceneops_core/jobs/schemas/results/episodes.py`
  -- additive `ExportLearningDataJobResult.base_export_id`,
  `.reused_shard_counts`, `.new_shard_counts`.
- `packages/sceneops-analytics/sceneops_analytics/learning_tables_sharded.py`
  -- `start_shard_index` param on `_write_sharded_table`/
  `write_sharded_learning_tables`; new
  `write_incremental_sharded_learning_tables`.
- `packages/sceneops-analytics/sceneops_analytics/writer.py` -- new
  `AnalyticsTableWriter.read_learning_table` (whole-file read-back, used
  only for the small `learning_episodes` table's incremental merge --
  never for sharded tables, which stay selectively read per Request 5.3).
- `packages/sceneops-analytics/sceneops_analytics/__init__.py` -- exports
  `write_incremental_sharded_learning_tables`.
- `apps/worker/sceneops_worker/jobs/dataset/export_learning_data.py` --
  the incremental branch: base-manifest resolution
  (`_resolve_base_export_manifest`,
  `BaseLearningExportNotFoundError`), revision merge, table-set
  consistency validation (§61), `learning_episodes` merge (§57),
  incremental shard writing, reused-shard ArtifactRecord skip (§62).
- `docs/architecture/learning-data-scaling-baseline.md` -- this section.

## 61. Table-set consistency guard

Not explicitly requested, but a gap discovered during design: nothing
stops a caller from requesting an incremental export whose `tables` param
omits a sharded table the base already included (or the reverse). Either
would silently produce a manifest whose declared table set disagrees
with what the base already committed to. Guarded explicitly in the job
handler: for each of `learning_steps`/`learning_signals`, if the base has
shards for that table, the incremental request must also request it
(and vice versa) -- otherwise a clear `ValueError` directs the caller to
a full export instead. This keeps every incremental export's table set
identical to its base's, which is what makes `write_incremental_
sharded_learning_tables`'s unconditional reuse of both of the plan's
shard lists correct without further per-table conditionals.

## 62. ArtifactRecord/lineage behavior

- **New physical objects get new records, exactly as a full export
  always has**: the merged `learning_episodes` file (always rewritten --
  see below), each newly-written shard, and the new manifest.
- **Reused shards get zero new records.** The job handler collects every
  reused shard's `uri` from the plan
  (`plan.reused_learning_steps_shards`/`reused_learning_signals_shards`)
  into a set, then skips `artifact_record_store.create()` for any shard
  in `shard_index` whose `uri` is in that set -- the base export's own
  record remains the sole, correct lineage entry. No upsert-by-checksum
  mechanism exists in `ArtifactRepository` (confirmed by reading it
  directly, §60 audit) or was needed -- "reuse" is simply "don't call
  `.create()` again."
- **`learning_episodes` is always fully rewritten as one new file**,
  even though most of its rows are unchanged -- a deliberate exception to
  "never rewrite unchanged data," justified because this table is always
  metadata-scale (one row per exposed EpisodeRef, never sharded) and
  because merging by concatenation (base's existing rows + a freshly-
  built delta-only sub-table) is far simpler and more auditable than
  trying to patch an existing Parquet file in place. Reused rows keep
  their **original** `export_id`/`dataset_id`/`dataset_version` column
  values unchanged (never rewritten to the new export's own identifiers)
  -- verified `SceneOpsDataset.open()` never reads these three columns
  for anything (it reads `episode_id`/`aligned_artifact_checksum` plus
  the metadata columns), so preserving them is both simpler and more
  honest provenance than overwriting them.
- The new manifest's own `base_export_id` field (§60) records the
  lineage edge at the manifest level too, purely informational.

## 63. Atomicity / retry model

Mirrors the existing full-export model exactly -- no new distributed-
transaction machinery was added or needed:

- Every Parquet/manifest write happens first; `context.commit()` (the
  DB-side ArtifactRecord commit) remains the single commit boundary, at
  the very end, unchanged in position or semantics.
- **A failure at any point before that final commit leaves the base
  export exactly as valid as it was before the incremental job ran** --
  the base's shards/manifest/records are never touched, read-only inputs
  to this job. A crash mid-write leaves orphaned new Parquet objects on
  disk (partial delta artifacts) but zero ArtifactRecords pointing at
  them and zero references to them from any manifest -- indistinguishable
  from garbage, never "partially complete" from any reader's perspective,
  since nothing durable (a DB record, a manifest a caller could resolve)
  ever pointed at them.
- **Retries are deterministic for the same reason full-export retries
  already were**: `export_id` is a pure content hash
  (`learning_data_export_id`, unchanged, frozen) of the *merged* checksum
  set, so retrying an incremental job with the same `base_export_id` and
  the same delta recomputes the identical `export_id`, writes to the
  identical content-addressed URIs, and (if the first attempt partially
  wrote some delta shards before crashing) simply overwrites them with
  byte-identical content -- never a duplicate-with-different-bytes state.
- No two-phase commit, no saga, no new scheduler was introduced --
  exactly per this request's explicit non-goals.

## 64. Benchmark results (write amplification)

`scripts/dev/benchmark_incremental_export.py`, base = 1000 episodes
(default shard policy, realistic length-jitter fixture), against a real
`LocalArtifactStore` via `CountingArtifactStore` (actual `write_bytes`
calls/bytes, not inferred from file sizes):

| scenario | reused shards | new shards | rewritten | bytes reused | bytes written (incremental) | bytes written (full rebuild) | write amplification |
|---|---|---|---|---|---|---|---|
| +10 new | 10 | 2 | 0 | 13,945,492 | 145,700 | 14,040,571 | **0.010** |
| +100 new | 10 | 2 | 0 | 13,945,492 | 1,413,714 | 15,306,583 | **0.092** |
| +10 revisions | 10 | 2 | 0 | 13,945,492 | 156,404 | 14,051,218 | **0.011** |
| mixed (+10 new, +10 revisions) | 10 | 2 | 0 | 13,945,492 | 298,831 | 14,187,638 | **0.021** |

(`new shards` = 2 because the delta is small enough in every scenario to
land in one new `learning_steps` shard + one new `learning_signals`
shard under the default policy's 200-episodes/200,000-rows bounds;
`rewritten` is always 0 by construction -- append-only never rewrites.)
Incremental export wrote **1-9% of the bytes** a full rebuild of the
same target set would have, confirming the core design bet: cost scales
with delta size, not base size, at this scale. `write_bytes` **call**
count for the incremental path was 2 in every scenario (one call per new
shard) versus 12 for a full rebuild (10 base-sized shards worth of
churn at this scale) -- fewer objects touched, not just fewer bytes.
`+10 revisions`' `target_episode_count` stays at 1000 (unique
`episode_id`s unchanged) while still writing 10 new revision rows/shards
worth of data -- the expected signature of "new revision, not new
episode" (§57).

## 65. Reader equivalence

`apps/worker/tests/jobs/test_export_learning_data_incremental.py::
test_full_build_and_incremental_export_expose_identical_logical_data`:
runs the real `ExportLearningDataJobHandler` twice over the same final
5-episode target set -- once as a single full export, once as a base
(3 episodes) plus an incremental follow-up (2 episodes) -- then opens
`SceneOpsDataset.open()` over both resulting manifests and asserts
`episodes()` returns the identical sorted `EpisodeRef` list, and every
`get_step(ref, step_index)` result is pairwise equal between the two
datasets, for every episode and every step. **Zero incremental-specific
code exists anywhere in `sceneops_analytics/learning_dataset/` --**
confirmed by grep as well as by this test: `SceneOpsDataset.open()`
doesn't know or care whether a manifest's shards came from one export
call or were assembled from a base plus a delta; it only ever reads
`shard_index`/`table_uris`, which are always already complete.

## 66. Verification results

- `make test` -- **1,333 passed, 5 skipped** (up from Request 5.4's
  1,322 -- 11 new tests: 7 planner unit tests +
  4 job-handler/reader-equivalence tests).
- `make lint` -- all checks passed.
- `make test-integration` -- 46 passed, unchanged from Request 5.4 (no
  new integration-tier tests added -- the incremental-export benchmark,
  §64, is a manually-run script against a real `LocalArtifactStore`, not
  a pytest integration test needing real Postgres/MinIO).
- `make lerobot-test` -- 36 passed, unchanged.
- `make e2e-lerobot-container` -- **PASSED** (fresh container run):
  `exported_episode_count=3, exported_step_count=22,
  total_frames_readback=22` -- identical to Request 5.4's numbers,
  confirming the incremental-export addition doesn't regress the real
  container/adapter round-trip (this fixture is a v1, non-incremental,
  single-export-call path, so it specifically re-confirms that path is
  untouched).

## 67. Remaining limitations / deferred, and boundary for Request 5.6

- **No compaction.** Many small incremental deltas over time leave many
  small trailing shards (§58's accepted cost) -- there is no operation in
  this request that merges/repacks them back toward `ShardPolicy`'s
  target size. Explicitly out of scope (task's own non-goal); a future
  request would need to design compaction as a distinct operation (likely
  itself needing its own planning/atomicity story, since it *would*
  rewrite existing shards, unlike anything here).
- **No deletion/tombstones.** An EpisodeRef, once exposed by any export,
  can never be removed via this mechanism -- by design (task's own
  non-goal), but worth restating as a hard boundary: any future
  "supersede/retract an episode" requirement needs new machinery this
  request deliberately does not provide.
- **Table-set consistency (§61) requires an incremental export's `tables`
  param to exactly match its base's** -- a caller cannot use this
  mechanism to *add* a previously-unexported table (e.g. base only ever
  built `learning_steps`, now also wants `learning_signals`) to an
  existing export lineage; that would require a full export today.
- **`learning_episodes`' merge-by-full-rewrite (§62)** is metadata-scale
  and cheap today, but if EpisodeRef counts grow into the hundreds of
  thousands, this table's size (still a single, ever-growing file) would
  eventually deserve its own sharding story -- not attempted here, since
  Request 5.2 explicitly scoped `learning_episodes` as always single-file
  and nothing in this request's measurements showed that assumption
  breaking.
- **Boundary for Request 5.6**: this request leaves a working, measured,
  atomic incremental-export path with append-only delta shards, full
  ArtifactRecord/lineage reuse, and proven reader equivalence.
  Request 5.6 (or later) inherits this as a second, coexisting export
  mode -- not a replacement for full export, which remains available and
  unchanged. Everything frozen through Request 5.4 (§55) remains frozen;
  additionally now frozen: `IncrementalExportPlan`'s shape and the
  append-only shard-reuse strategy (a future request wanting compaction
  must add a new, distinct operation rather than change what this one
  does).

---

# Request 5.6: Scale Benchmark & Distributed Boundary

Everything below is new; §1-67 above (Requests 5.1-5.5) are historical
record and unchanged. This request adds no new production code path --
it validates the architecture built across Requests 5.1-5.5 at larger
scale, audits the one bottleneck Request 5.4 identified but did not fix
(Python object reconstruction), and defines, in writing only, the
boundary between the current single-node PyArrow/Polars architecture and
any future distributed system. Spark is not introduced.

## 68. Final scale benchmark: ladder and methodology

`scripts/dev/benchmark_phase5_final.py` -- a new, consolidated benchmark
combining every workload category Requests 5.1/5.3/5.4/5.5 measured
separately, run together against one instrumented store per scale so
every number below comes from the same harness, same run, same
methodology:

- **Ladder**: `tiny`/`small`/`medium`/`large` (unchanged, `DEFAULT_SCALE_LADDER`,
  10/100/1,000/10,000 episodes) plus one new **`xlarge`** tier defined for
  this request (25,000 episodes, same per-episode shape as `large`) --
  intended to confirm the scaling invariants (§71) continue to hold at
  2.5x `large`'s episode count.
- **`xlarge` was attempted and could not be completed within this
  environment's execution-time ceiling** (a single command, foreground or
  background, is capped at 10 minutes here). A standalone timing of
  `large`'s fixture generation alone (`write_scaled_dataset_artifacts`,
  no workloads yet) took 206s; `xlarge`'s harness run needs that same
  fixture-generation step **three times** (once for the base dataset,
  once for the standalone full-export-write phase, once for the
  incremental delta's target spec) plus five O(episode-count) workload
  phases on top -- well past 10 minutes in total. This is a **benchmark-
  harness** ceiling, not a production-architecture one: `build_scaled_entries`
  is an unvectorized, pure-Python per-row fixture generator (already
  flagged as this harness's own practical ceiling in Request 5.2 §9's "at
  what scale would this break" analysis and Request 5.2's scale-ladder
  docstring), not the production `AlignedEpisodeArtifact` resolution path,
  which never regenerates already-resolved artifacts from scratch.
  `xlarge` is therefore excluded from the measured ladder below; the
  4-tier ladder (a 1,000x episode-count span, tiny→large) plus one
  targeted, cheaper follow-up check (§71, cache-boundedness at `large`
  after touching all 10,050 episodes) is what this request's invariant
  validation (§71) is actually based on. This itself is not a wasted data
  point -- see §74 for what a benchmark harness "running out of practical
  time to even generate its input" implies about where real distributed
  processing would first become relevant, versus where it plainly would
  not.
- **Instrumentation**: every phase runs against a new
  `_TimedCountingArtifactStore` (benchmark-only, this script) that
  records call/byte counts (mirrors `CountingArtifactStore`) *and*
  wall-clock time spent inside every awaited `ArtifactStore` call.
  Per phase: `io_wall_seconds` (time inside the store) and
  `reconstruction_seconds = wall_seconds - io_wall_seconds` (everything
  else -- Parquet decode, Python/Pydantic object construction, Polars/
  PyArrow table construction) are both reported, satisfying this
  request's "separate I/O, Python reconstruction, and writer/build costs"
  requirement without touching any production code.
- **Workloads measured, every scale**: dataset open + enumerate refs;
  cold single-episode full window; warm repeated access (5x) to the same
  episode; cold second-episode access; `SequenceSampler.create()`; full
  sampler window iteration; cold-open + full/high-density dataset
  iteration; full export write (fresh `export_id`, entire entry set,
  `write_sharded_learning_tables`); incremental export write (~1% new
  episodes, `plan_incremental_export` + `write_incremental_sharded_learning_tables`,
  same pattern as Request 5.5's own benchmark but now at every scale
  instead of one).
- **Python reconstruction isolation** (§72): for the two largest measured
  scales (`medium`, `large`), a separate, targeted measurement fetches
  each of 50 sampled episodes' raw `learning_steps`/`learning_signals`
  Arrow row groups (`read_episode_row_group`, real I/O + Parquet decode)
  and times that separately from calling `_build_steps_from_tables`
  (pure Python/Pydantic `LearningStep` graph construction) on the
  already-fetched tables -- a second, independent confirmation of the
  `io_wall_seconds`/`reconstruction_seconds` split above, isolating the
  one specific step (`LearningStep` object construction) Request 5.4
  identified as the dominant cost.

## 69. Final scaling measurements

Wall time per workload (seconds), tiny→large (10 → 10,000 episodes, a
1,000x span):

| scale | episodes | A open | B cold 1-ep | C warm x5 | D cold 2nd-ep | E sampler.create | F iterate all windows | G full iteration | H full export write | I incremental write (Δ) |
|---|---|---|---|---|---|---|---|---|---|---|
| tiny | 10 | 0.003 | 0.022 | 0.016 | 0.019 | 0.193 | 0.052 | 0.299 | 0.228 | 0.027 (+1) |
| small | 100 | 0.007 | 0.033 | 0.029 | 0.031 | 4.376 | 5.354 | 5.716 | 4.281 | 0.100 (+1) |
| medium | 1,000 | 0.062 | 0.033 | 0.019 | 0.021 | 24.926 | 34.837 | 36.276 | 26.352 | 0.276 (+10) |
| large | 10,000 | 0.645 | 0.028 | 0.008 | 0.013 | 102.936 | 149.843 | 162.162 | 107.966 | 1.012 (+100) |

Object/storage footprint and peak process memory, same runs:

| scale | object count | shards (steps/signals) | total storage bytes | ru_maxrss after scale |
|---|---|---|---|---|
| tiny | 4 | 1 / 1 | 153,915 | 305 MB |
| small | 4 | 1 / 1 | 1,782,983 | 1,411 MB |
| medium | 14 | 6 / 6 | 11,818,025 | 2,707 MB |
| large | 104 | 51 / 51 | 94,329,310 | 4,508 MB |

I/O split (`io_wall_seconds` vs `reconstruction_seconds`) for the two
most expensive workloads at each scale:

| scale | E sampler.create (io / recon) | G full iteration (io / recon) |
|---|---|---|
| tiny | 0.0003s / 0.193s | 0.004s / 0.295s |
| small | 0.0006s / 4.375s | 0.034s / 5.682s |
| medium | 0.015s / 24.911s | 0.339s / 35.937s |
| large | 0.148s / 102.788s | 2.835s / 159.327s |

At every scale, **I/O is 0.1-2% of total wall time; reconstruction is
98-99.9%** -- and this ratio does not improve at bigger scale (large's
I/O share is actually *smaller* proportionally than tiny's), confirming
this is a structural property of the current object-reconstruction path,
not a cold-start artifact that amortizes away.

`E` (bulk schema resolution) I/O calls track **shard count**, not
episode count -- reconfirming §46's finding at a fresh run: 2 calls at
tiny/small (1 shard each, unaffected by 10x more episodes), 31 calls at
medium (6 shards), 301 calls at large (51 shards); ~5-6 calls/shard is
stable across both. `F` (exhaustive per-episode iteration) necessarily
scales with episode count instead (2,080 calls at medium, 20,100 at
large, ~2/episode both times) -- expected, since touching every
episode's own data once is an O(n) operation by nature, distinct from
the shard-count invariant, which applies to *bulk metadata* operations
only (§71).

## 70. Bottleneck classification by workload

| Workload | Dominant bottleneck | Evidence |
|---|---|---|
| `A` open + enumerate | learning-table construction (reading `learning_episodes.parquet`, small) | sub-millisecond to ~0.6s even at 10,000 episodes; scales with episode *metadata* row count only |
| `B`/`D` cold single/second episode | Parquet decoding + Python reconstruction (I/O negligible) | §69's io/recon split: <1ms io, 20-30ms recon, flat across scale (§71 invariant 1) |
| `C` warm repeated access | Python reconstruction only (zero I/O, `_episode_steps_cache` hit) | `io_wall_seconds=0.000` at every scale |
| `E` `SequenceSampler.create()` | **Python object reconstruction**, overwhelmingly | 98-99.9% of wall time is `reconstruction_seconds` at every scale (§69); I/O call count tracks shard count, already optimal |
| `F`/`G` full/high-density iteration | **Python object reconstruction**, overwhelmingly | same split; this is the workload real training-data consumption resembles most closely, and it is reconstruction-bound, not I/O-bound, at every measured scale |
| `H` full export write | learning-table construction (Polars/PyArrow table building, zstd encoding) + Python fixture resolution | `io_wall_seconds` is 0.1-0.3% of `H`'s wall time at every scale; the rest is `build_learning_steps_table`/`build_learning_signals_table` (Request 2.5's pure-Python-per-row builders, already flagged in §6/Request 5.1) plus PyArrow's Parquet write |
| `I` incremental export write | export writing, proportional to delta size only | wall time scales with *delta* episode count (1/1/10/100), not base size -- 0.027s→1.012s across a 10,000x base-size range while delta only grew 100x (§71 invariant 3) |
| storage/object count | never independently limiting at any measured scale | object count (4→104) stays far below any practical object-store limit; shard count (1→51) grows sublinearly relative to episode count (10→10,000) under the bounded shard policy |
| network round trips | never independently limiting | `LocalArtifactStore` has no network cost by construction; Request 5.4 §48's real-MinIO measurement (unchanged, not re-run here) already characterized this separately and found the `run_coroutine_threadsafe` bridge overhead (+11.3%), not round-trip count, as the residual real-network cost |

No workload's bottleneck is attributable to distributed processing,
Spark, or anything this benchmark cannot already explain with a specific,
measured Python/PyArrow/Polars call.

## 71. Scaling invariants: validated

All four hold, with direct measurement at up to 10,000 episodes (and, for
cache-boundedness, a dedicated 10,050-episode check):

1. **Single-Episode I/O does not grow with total dataset bytes.**
   `B`'s range-read bytes: tiny 102,120 B → small 499,428 B → medium
   783,581 B → **large 775,756 B** -- flat from `small` onward (the small
   variation reflects each scale's own per-episode step/channel shape,
   not total dataset size) while total storage grew 153,915 B → 94,329,310 B,
   a **613x** span. Directly reconfirms §35's reduction-factor table
   (1.4x → 3.6x → 15.1x → **121.6x** at `large`, using the identical
   numbers) at this request's fresh run.
2. **Process cache memory remains bounded by CachePolicy.** Direct check:
   opened a fresh `large`-scale dataset (default `CachePolicy`,
   `max_episode_steps=64`) and called `get_window` for its full step
   range on **every one of 10,050 exposed EpisodeRefs** (every revision,
   not just 10,000 distinct episode_ids). Result: `_episode_steps_cache`
   length == exactly **64** (its bound), `_schema_cache` == exactly
   **128** (its bound), `_shard_metadata_cache` == **102** (2 entries/
   shard x 51 shards, itself bounded by shard count, well under its 256
   bound) -- after touching 157x more episodes than the cache's own
   bound. **Holds.**
3. **Incremental write cost scales primarily with delta size.** `I`'s
   wall time: 0.027s (Δ1 episode, base=10) → 0.100s (Δ1, base=100) →
   0.276s (Δ10, base=1,000) → 1.012s (Δ100, base=10,000). Base size grew
   1,000x (10→10,000); delta size grew 100x (1→100); wall time grew
   ~37x -- tracking delta size (sublinearly, since larger scales' shorter
   `steps_per_episode` partially offsets per-new-episode cost), never
   base size. New shard count stayed at exactly **2** (one
   `learning_steps` + one `learning_signals` shard) at every scale
   despite base shard count growing 1→51 -- reused shard count grows
   with base size (1→1→12→102), confirming write cost is decoupled from
   how large the thing being incrementally extended already is. Directly
   reconfirms Request 5.5 §64's write-amplification benchmark (0.010-0.092
   at 1,000 episodes) at a 10x larger base.
4. **Bulk I/O calls scale with shard count rather than EpisodeRef count.**
   §69/§70's `E` numbers: 2 calls (1 shard) at both tiny (10 episodes)
   and small (100 episodes) -- **identical call count despite 10x more
   episodes**, because both fit in the same one shard. 31 calls (6
   shards) at medium, 301 calls (51 shards) -- ratio stable at ~5-6
   calls/shard across a 8.5x shard-count range. Directly reconfirms §46's
   finding (2,035→35 calls at medium for the pre-5.4→post-5.4 transition;
   305 calls at `large` in that request's own measurement) at this
   request's fresh run (31/301 vs the original 35/305 -- small
   differences attributable to this benchmark's own horizon/sampling
   parameters, not a regression).

No invariant failed at any measured scale.

## 72. Python reconstruction bottleneck (audit)

Request 5.4 (§50, qualitatively) found large-scale `SequenceSampler.create()`
dominated by `LearningStep` object-graph construction, not I/O, but did
not isolate the two costs directly. This request does, via a dedicated
measurement (§68) separate from the `io_wall_seconds`/`reconstruction_seconds`
split above: fetch each sampled episode's raw Arrow row groups
(`read_episode_row_group`, real I/O + Parquet decode -- but *not* Python
object construction, since PyArrow's `Table.to_pylist()` inside
`_build_steps_from_tables` is what actually builds Python dicts/objects)
separately from timing `_build_steps_from_tables` itself on the
already-fetched tables:

| scale | sample size | I/O+decode/episode | Python reconstruction/episode | reconstruction's share |
|---|---|---|---|---|
| tiny | 13 (all) | 1.96 ms | 13.56 ms | 87.4% |
| small | 50 | 2.86 ms | 32.66 ms | 92.0% |
| medium | 50 | 2.08 ms | 17.32 ms | 89.3% |
| large | 50 | 1.69 ms | 6.62 ms | 79.7% |

Confirms, with a direct isolated measurement rather than an inference
from total wall time, that **pure Python/Pydantic object-graph
construction is 80-92% of the per-episode cost, at every scale** -- I/O
and Parquet decode together are a small, roughly-constant few
milliseconds per episode regardless of dataset size (consistent with
invariant 1, §71). The per-episode absolute cost *shrinks* from small to
large (32.7ms → 6.6ms) because larger scales in this ladder have fewer
`steps_per_episode` (100 at small vs 30 at large) -- reconstruction cost
is proportional to *step+signal row count per episode*, not to which
scale tier it came from.

**Recommended next optimization target** (not implemented here, per this
request's own "no major semantic rewrite" constraint): the task's three
candidates, evaluated against this measurement --

- **Lighter intermediate representation** -- the most promising, lowest-
  risk option. `LearningStep`/`AlignedSignal` are full Pydantic models
  (validation, field aliasing, `__init__` overhead) built fresh per row;
  a `SequenceSample`'s actual consumer (`FeatureProjection` resolution,
  §37) only ever reads a small, fixed set of fields per signal to
  assemble a dense vector. A lighter, validation-free intermediate
  (e.g. plain tuples/slotted dataclasses, or reading directly off Arrow
  arrays without a per-row Python dict roundtrip) between "Arrow table"
  and "the dense vector `FeatureProjection` actually produces" would cut
  the exact cost this measurement isolates, without touching
  `LearningStep`'s own public/frozen shape (§ frozen semantics) if scoped
  as an internal reconstruction-path change only.
- **Vectorized table construction** -- addresses `H`'s writer-side cost
  (Request 2.5's per-row `build_learning_steps_table`/
  `build_learning_signals_table`, already flagged in Request 5.1 §6),
  not this measurement's *read*-side cost directly; a real but separate
  optimization target from the one this audit isolates.
- **Arrow-native/columnar processing longer in the pipeline** -- the
  largest-scope option (would mean `FeatureProjection` resolution and
  `SequenceSample` assembly operating on Arrow arrays directly, deferring
  Python object construction past where it happens today, or removing it
  for the hot path entirely) -- correctly out of scope for "no major
  semantic rewrite," but the direction this measurement's numbers point
  toward if reconstruction cost ever needs to shrink by more than a
  constant factor.

This request does not implement any of the three -- consistent with its
own "no major semantic rewrite" constraint -- but the isolated
measurement above gives whoever picks this up next a concrete number to
beat (6.6-32.7ms/episode of pure Python construction) rather than a
qualitative impression.

## 73. Two remaining Phase 5 semantics: reviewed and confirmed

**`learning_episodes.export_id` (and `.dataset_id`/`.dataset_version`)
for rows reused through an incremental export**: confirmed, by direct
grep of `sceneops_analytics/learning_dataset/dataset.py`, that
`SceneOpsDataset` never reads these three per-row columns for anything
(only `learning_manifest.export_id`, the manifest's own field, is ever
read -- for error messages and the shard-index/table-set consistency
checks, never for row filtering or identity). The confirmed, now-frozen
semantic: these columns mean **"the export_id/dataset_id/dataset_version
of the export call that originally wrote this exact row's bytes"** --
first-write provenance, not "which export currently exposes this row."
For a base export's own rows, reused verbatim by any number of later
incremental exports, this value stays permanently pinned to the base's
own `export_id`, exactly as Request 5.5 §62 implemented it (never
rewritten on reuse). The *current, authoritative* answer to "which
export exposes this row" is always the `LearningDataExportManifest` you
opened (its own `.export_id`, plus its `.inputs`, which correctly grows
on every incremental export) -- never the per-row column. This mirrors,
at the row-column layer, the same "reuse means never re-labeling"
principle already applied at the `ArtifactRecord` layer (§62) --
consistent within this request, and now an explicit, documented
invariant for any future code (tooling, future compaction) to respect:
**do not infer current export membership from these three columns.**

**Append-only incremental shard fragmentation**: confirmed to require
only a future, optional compaction operation, not any change to current
incremental semantics. Reasoning, reconfirmed against this request's own
measurements: (1) `plan_incremental_export`'s reuse decision is a pure
set-membership diff under the addition-only precondition (Request 5.5
§58) -- completely insensitive to how many prior increments produced the
base's shard list, so an arbitrarily fragmented base plans identically
to a freshly-compacted one; (2) reader correctness (§65's equivalence
test) depends only on `shard_index`'s contents being self-consistent,
never on shard *count* or *size distribution*; (3) the one place
fragmentation could eventually matter is performance, not correctness:
`_shard_metadata_cache`'s bound (256 by default) is sized against shard
count (§71 invariant 2's `102 entries / 51 shards` measurement), so many
more, smaller shards from repeated small increments would mean more
distinct shard-metadata cache entries competing for that same bound,
plausibly increasing footer-refetch rate under a very fragmented history
-- a real, but purely quantitative, future motivation for compaction, not
a correctness gap today. No compaction was implemented (none was
warranted -- this request's own constraint, "do not implement compaction
unless a correctness issue is discovered," and none was).

## 74. Distributed-processing boundary

**What remains appropriate for single-node PyArrow/Polars, based on
§69-72's measurements**: essentially everything this benchmark
exercises, at every scale measured (up to 10,000 episodes / 94 MB / 104
objects). Every workload's bottleneck (§70) resolved to a specific,
already-understood, already-optimized-where-warranted Python/PyArrow/
Polars cost -- never to "too much data for one machine to hold or
process." Reads (selective, bulk, and full/exhaustive iteration),
exports (full and incremental), and the shard/manifest metadata layer
all stayed single-node-appropriate through this request's entire tested
range. Peak process memory (`ru_maxrss`, §69) grew from 305 MB to 4,508
MB across the ladder -- comfortably within a single modern machine at
every measured point, and growing with *touched working set*
(`CachePolicy`-bounded, §71 invariant 2), not with total dataset size.

**Which workloads could eventually justify Spark or another distributed
engine**: none of the workloads this request measured, at the scales
measured. The one candidate this request's own methodology surfaced is
narrower and more specific than "large datasets" in general: **the
per-row Python table-construction step** (`build_learning_steps_table`/
`build_learning_signals_table`, §70's `H` bottleneck, and this request's
own benchmark-harness fixture generator, `build_scaled_entries`, hitting
this environment's execution-time ceiling at `xlarge`, §68) is the one
place this investigation found itself running out of practical headroom
-- and it is an unvectorized per-row Python loop, not an inherently
distributed-shaped problem. A **vectorized rewrite of the same
single-node code** (§72's "vectorized table construction" candidate)
would very plausibly move that same headroom limit out by an order of
magnitude or more before distributed processing would need to be
considered for it at all -- so even this request's own closest brush
with a real scaling wall points first at a single-node fix, not at
Spark.

**Is distributed execution needed for reads, exports, transformations,
or only very large offline batch processing?** Based on measurements:
not for reads (selective + bulk, §71 invariants 1/4), not for
incremental export (§71 invariant 3, cost decoupled from base size), and
not for full export at any measured scale (`H`'s cost is Python/PyArrow-
bound, not distributed-shaped, §70). The only category where this
request's own data leaves the question open is **very large, one-time,
offline batch construction of a full export from scratch** (`H`/`build_seconds`)
at scales this request's own harness could not reach (`xlarge`+) --
and even there, per the previous paragraph, a vectorized single-node
rewrite is the evidence-supported next step to try before a distributed
one.

**What dataset/workload characteristics should trigger reconsideration**
(connected to this request's measured CPU/memory/I/O behavior, not an
arbitrary size number):

- **CPU-bound reconstruction time for a single logical operation
  (a full export write, or a full-dataset training pass) exceeds what a
  single machine can complete in the caller's required wall-clock
  budget**, *after* the vectorization opportunities §72 identifies have
  already been exhausted -- e.g. if `H`'s ~26s/1,000-episodes rate (§69)
  still held (or worsened) after removing the per-row Python
  construction cost, at whatever episode count the caller's actual
  export-latency requirement demands.
- **Peak working-set memory** (not total dataset size -- `CachePolicy`
  already decouples those, invariant 2) **exceeds single-machine RAM for
  a required access pattern** -- e.g. a training job that must genuinely
  hold far more than `CachePolicy`'s bound of reconstructed episodes
  resident simultaneously (this request found no such requirement in any
  measured workload; `CachePolicy`'s bounds were sized against realistic
  per-episode memory footprint in Request 5.4 §42, and reconfirmed still
  correctly bounding memory here at 10,050 episodes).
- **Object/shard count grows enough to make the in-memory shard-lookup
  index itself, or per-shard metadata caching, the bottleneck** --
  not observed here (104 objects, 51 shards at `large`; §70 confirms
  storage/object count was never independently limiting at any measured
  scale), but a real, specific, measurable trigger a future request
  could check for directly (shard-lookup dict size, `_shard_metadata_cache`
  hit rate) rather than guessing from total episode count.

**Is Spark currently justified?** **No.** Nothing measured in this
request -- across a 1,000x episode-count span, four workload categories,
and both full and incremental export -- surfaced a bottleneck that a
distributed engine would resolve and a single-node fix would not. The
one place this request's own tooling ran out of headroom (`xlarge`
fixture generation, §68) is a known, already-diagnosed (Request 5.1 §6,
Request 5.2 §9), unvectorized single-node Python loop -- exactly the
kind of problem Spark is the wrong tool for, not the right one.
Introducing Spark now would add a second execution engine, a second
deployment/ops surface, and a new class of distributed-correctness
concerns (partitioning, shuffle, exactly-once semantics) to solve a
problem this request's own measurements show does not yet exist.

## 75. Phase 5 performance summary

Before → after, Request 5.1's original baseline finding through this
request's own fresh measurement, same benchmark methodology throughout
(`CountingArtifactStore`/`_TimedCountingArtifactStore`, real Parquet, real
`AlignedEpisodeArtifact` contracts, no mocked I/O):

| Dimension | Before | After | Evidence |
|---|---|---|---|
| **Whole-table → selective read** | One EpisodeRef access reads 100% of `learning_steps`+`learning_signals`, every scale (Request 5.1 §5/§6) | One EpisodeRef access reads only its own shard's row group -- **121.6x fewer bytes at `large`** (94.3 MB → 776 KB), and the reduction factor *grows* with scale (1.4x → 3.6x → 15.1x → 121.6x, tiny→large) | §35, reconfirmed §71 invariant 1 |
| **Episode-count I/O → shard-count bulk I/O** | Bulk schema resolution: 1 selective fetch per contributing episode (2,035 calls at medium/1,000 episodes) | 1 combined fetch per contributing *shard* (35 calls at medium/6 shards; **58x fewer calls**); confirmed unchanged at 10x scale (301 calls / 51 shards at `large`, ~stable calls/shard ratio) | §46, reconfirmed §71 invariant 4 |
| **Unbounded → bounded cache** | `_episode_steps_cache`/`_schema_cache` grow with every distinct episode ever touched, no eviction (Request 5.4 §42 audit) | Bounded by `CachePolicy` (64/128/256 default) regardless of episodes touched -- confirmed still exactly at-bound after touching **10,050** episodes (157x the cache's own size) at `large` scale | §44, reconfirmed §71 invariant 2 |
| **Full rebuild → incremental shard reuse** | Any new/changed EpisodeRef rewrites 100% of `learning_steps`/`learning_signals` shards | Write amplification **0.010-0.092** (1-9% of full-rebuild bytes) at 1,000 episodes; reused-shard count tracks base size (1→1→12→102) while new-shard count stays flat (2) regardless of base size, confirmed at 10,000-episode base | Request 5.5 §64, reconfirmed §71 invariant 3 |

The strongest single number from each request: **5.1** -- established
the 100%-of-table baseline this whole phase improves on; **5.2** --
shard/row-group physical layout, the enabling change for everything
after it; **5.3** -- 121.6x byte reduction for single-episode access at
`large`; **5.4** -- 58x fewer bulk I/O calls, cache exactly bounded
after 10,050-episode exhaustive touch; **5.5** -- 0.010 write
amplification (99% bytes saved) for a +10-episode incremental export at
1,000-episode scale; **5.6** -- every one of the above reconfirmed at up
to 10,000 episodes with zero invariant failures, plus a first direct
isolation of the remaining bottleneck (Python reconstruction, 80-92% of
per-episode cost, §72).

## 76. Verification results

- `make test` -- all passing (see final count in this request's closing
  summary; no test changed or added here beyond what already existed --
  this request is benchmark/analysis/documentation only, no production
  code changed).
- `make lint` -- all checks passed.
- `make test-integration` -- unchanged, all passing.
- `make lerobot-test` -- unchanged, all passing.
- `make e2e-lerobot-container` -- unchanged, PASSED (this request touches
  no export/read/write code path this fixture exercises).
- Final scale benchmark suite (`scripts/dev/benchmark_phase5_final.py`,
  `tiny,small,medium,large`) -- results in §69-72 above; raw JSON
  available via `--out`.

## 77. Remaining Phase 5 limitations

- **`xlarge` (25,000 episodes) could not be run in this environment**
  (§68) -- a benchmark-harness fixture-generation ceiling, not a
  production one; the 4-tier ladder plus the dedicated 10,050-episode
  cache-bound check (§71 invariant 2) is what this request's conclusions
  are actually based on, and no invariant showed any sign of degrading
  as episode count grew across the tested 1,000x span.
- **The Python-reconstruction bottleneck (§72) is measured and
  attributed, not fixed** -- per this request's own "no major semantic
  rewrite" constraint. A lighter intermediate representation is the
  recommended next target, with a concrete number to beat
  (6.6-32.7ms/episode).
- **The writer-side per-row table construction cost** (`build_learning_steps_table`/
  `build_learning_signals_table`, §70's `H` bottleneck) remains
  unvectorized, unchanged since Request 5.1 §6 first flagged it --
  distinct from, but related to, the read-side reconstruction cost §72
  isolates.
- **No compaction exists** (Request 5.5 §66/§73 -- confirmed still not
  warranted; a purely quantitative, not correctness, motivation for a
  future request).
- **Real-network (MinIO) behavior was not re-measured at these larger
  scales** -- Request 5.4 §48's characterization (against one local
  MinIO instance) stands unchanged; this request's new measurements are
  all against `LocalArtifactStore`, consistent with every prior
  request's own local-benchmark tier.

## 78. Frozen boundary for Request 5.7

- Everything frozen through Request 5.5 (§67) remains frozen, unchanged
  by this request (no production code was modified here).
- Newly confirmed and now explicitly documented as frozen (§73):
  `learning_episodes.export_id`/`.dataset_id`/`.dataset_version` are
  first-write provenance only, never current-membership; no future code
  may infer current export membership from these columns.
- The Python-reconstruction cost (§72) and the writer-side per-row table
  construction cost (§70's `H`) are both now explicitly measured,
  attributed, and left as open, well-specified optimization targets for
  a future request -- not compaction, not deletion/tombstones, and not
  Spark or another distributed engine, per this request's own conclusion
  (§74) that no measured workload currently justifies one.
- Do not proceed to Request 5.7 in this session, per the standing
  instruction.
