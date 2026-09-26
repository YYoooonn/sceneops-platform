# Learning Data Scaling Baseline (Phase 5, Request 5.1)

> Audit + measured baseline for `SceneOpsDataset`'s physical storage/query
> path. No architecture changes in this request -- see
> [Robot learning data layer](./robot-learning-data.md) for the frozen
> Phase 2 domain contracts this audit does not touch, and
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
