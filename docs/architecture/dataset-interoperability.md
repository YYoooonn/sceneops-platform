# Dataset Interoperability (Phase 3)

> Describes Phase 3 as it exists today on `feat/dataset-interoperability`.
> Like [robot-learning-data.md](./robot-learning-data.md), this is a "what's
> actually built" document, not aspirational — every claim below was
> checked against the code and against a real, live run of
> `make e2e-lerobot` against Postgres/MinIO, not against the original
> request planning documents.

## 1. Purpose

Phase 2 ([Robot learning data layer](./robot-learning-data.md)) ended at
SceneOps' own native training-consumer layer
(`SequenceSampler`/`NumPySequenceSample`/`SceneOpsTorchDataset`) and
explicitly deferred external ecosystem integration (§9 of that doc). Phase
3 builds that deferred path: a framework-neutral external-adapter contract
over `SceneOpsDataset`, and one concrete implementation of it —
`SceneOpsDataset -> LeRobot`.

SceneOps' own learning-data representation (`AlignedEpisode`,
`SceneOpsDataset`, `FeatureSchema`, ...) remains canonical and
untouched by this phase. Phase 3 only adds a *projection* out of it into an
external format, and — symmetrically — a shared vocabulary
(`ExternalDatasetRef`) for describing a dataset that lives outside
SceneOps' model at all, on either the import or export side.

## 2. Identity model

Three identities recur through Phase 3 and must never be collapsed into
each other:

```text
ExternalDatasetRef
  A dataset representation OUTSIDE SceneOps' canonical model. Used for
  both an import source and an export target — there is deliberately one
  type, not a separate SourceDatasetRef/ExportDatasetRef pair. Import vs.
  export is a property of the *operation* that uses it, never of the
  ref's own shape.
  format / format_version / uri (+ optional external_name /
  external_revision / checksum)
  sceneops_core.datasets.ExternalDatasetRef

Dataset / DatasetVersion
  SceneOps' OWN canonical identity only. Never constrained by what an
  external format's SDK happens to require.
  dataset_id / version
  sceneops_core.datasets.schemas (DB-backed, see data-model.md §2)

source_format_version
  The external SOURCE format's own version string (e.g. nuscenes-devkit's
  on-disk "v1.0-mini" folder name), passed straight into that SDK. Kept
  fully separate from DATASET_VERSION -- conflating the two was a real
  bug found and fixed before Phase 3 (Request 3.2A/3.2B, see
  local-development.md's "Canonical identity vs. external source/export
  identity").
```

Worked example (the general shape this identity model supports —
`core`'s real nuScenes ingestion into SceneOps, followed by a
LeRobot export of that same canonical DatasetVersion):

```text
nuScenes v1.0-mini                          (ExternalDatasetRef, import source)
        | ingest (source_format_version="v1.0-mini")
        v
SceneOps test-e2e-core / test-v1            (DatasetVersion, SceneOps-canonical)
        | LeRobotDatasetAdapter.export(...)
        v
LeRobot v3                                  (ExternalDatasetRef, export target)
```

This is architecturally valid and exercised end-to-end by
`core`'s own E2E suite (ingestion side) plus the adapter's own tests
(export side) — but note the *specific, verified round-trip E2E*
(`make e2e-lerobot`, §6 below) runs against the separate, deterministic
`interop` fixture, not a live nuScenes-sourced `core` Episode. See §6 for
exactly what was proven end-to-end versus what is proven only
compositionally (each half tested, not yet chained in one E2E).

LeRobot's own export-side identity is exposed as an `ExternalDatasetRef`
too, via `LeRobotDatasetAdapter.dataset_ref`:
`format="lerobot"`, `format_version` (e.g. `"3.0"`, read from the
installed `lerobot` package's own `CODEBASE_VERSION` at call time, not
hardcoded), `uri` = the local export directory,
`external_name` = the LeRobot `repo_id`. It is never written anywhere —
computing it is free (pure construction from the adapter's own
constructor args), and nothing persists it (§9).

## 3. Final export architecture

```text
SceneOpsDataset                              (Phase 2, unchanged)
      |
      v
FeatureProjection                            (Phase 2's 2.7A contract, reused
      |                                        unchanged -- channel order,
      |                                        namespace, dense-shape rules)
      v
ExternalDatasetAdapter.export(dataset, config)
      |  sources exclusively from SceneOpsDataset -- never
      |  AlignedEpisodeArtifact/raw Parquet/SequenceSampler
      v
ExternalDatasetWriter                        initialize -> write_episode x N
      |                                       -> finalize
      v
concrete external format on disk             (LeRobot v3 today)
      |
      v
ExternalDatasetRef                           (describes the export target;
                                               never a SceneOps DatasetVersion)
```

`sceneops_analytics.external_adapters` (Request 3.1/3.1A) is the shared,
framework-neutral contract; `sceneops_analytics.external_adapters.lerobot`
(Request 3.3) is the one concrete implementation of it that exists today.

### Interoperability path vs. training path

```text
SceneOpsDataset
      |
      +-- Training path (Phase 2)
      |     SequenceSampler -> NumPy / Torch
      |     fixed-horizon, overlapping, stride-based windows;
      |     shuffled/sampled by the training consumer
      |
      +-- Interoperability path (Phase 3)
            ExternalDatasetAdapter -> ExternalDatasetWriter
            whole Episodes, export order, one step each, exactly once
```

These are deliberately separate, non-overlapping consumers of
`SceneOpsDataset`. `ExternalDatasetAdapter.export()` never uses
`SequenceSampler` — it walks `dataset.episodes()` directly and projects
each Episode's full step range via `get_window(ref, 0, step_count, ...)`,
once per Episode, in `dataset.episodes()`'s deterministic order (or the
caller's explicit `episode_refs` order). There is no windowing, no stride,
no overlap, and no shuffling anywhere in the export path — an external
adapter's output is one row per SceneOps step, never a
resampled/duplicated/shuffled view of it.

## 4. Adapter / writer contract (frozen, Request 3.1/3.1A)

- `EpisodeRef = episode_id + aligned_artifact_checksum` (Phase 2's own
  coordinate, reused unchanged) is the only Episode identity the export
  path ever uses — never `episode_id` alone, never an `ArtifactRecord` id.
  Two revisions of the same `episode_id` remain distinct, independently
  exported episodes.
- Episode and step ordering is fully deterministic: `dataset.episodes()`
  order (sorted `(episode_id, aligned_artifact_checksum)`) unless the
  caller supplies an explicit `episode_refs` list; within an Episode,
  `step_index` runs exactly `0..n-1` and `timestamp_us` strictly
  increases (`validate_step_ordering`).
- Feature projection is always explicit (`ExternalExportConfig.projection`,
  Phase 2's `FeatureProjection` reused unchanged) — channel order directly
  determines dense output order, never auto-sorted.
- One export requires one compatible dense `FeatureSchema` across every
  Episode it writes (checked once per Episode against the first resolved
  schema) — `ExternalFeatureSchemaMismatchError` otherwise, mirroring
  `SequenceSampler`'s own `SamplerSchemaMismatchError` (Phase 2).
- Every SemanticField (`EPISODE_IDENTITY`, `STEP_ORDERING`, `TIMESTAMPS`,
  `OBSERVATION_ACTION_NAMESPACE`, `FEATURE_ORDERING`,
  `TASK_OUTCOME_METADATA`, `SIGNAL_STATUS`,
  `SOURCE_REVISION_TRACEABILITY`) is classified as exactly one of
  `LOSSLESS` / `LOSSY_EXPLICIT` / `UNSUPPORTED`. A field a concrete
  adapter's `semantic_capabilities()` omits is treated as `UNSUPPORTED` —
  silence is never read as "lossless". `LOSSY_EXPLICIT`/`UNSUPPORTED`
  fields are always recorded on `ExternalExportReport.semantic_losses`;
  `unsupported_semantic_policy=FAIL` (the config default) additionally
  raises before any write happens, so an export can never silently drop a
  concept it cannot represent unless the caller explicitly opts into
  `RECORD`.
- Writer lifecycle is exactly `initialize() -> write_episode() x N (export
  order) -> finalize()`, guaranteed by `ExternalDatasetAdapter.export()`
  itself: `finalize()` runs only if every `write_episode()` call
  succeeded; any exception propagates immediately and skips every later
  lifecycle step.
- Output stays an `ExternalDatasetRef`, never a new SceneOps
  `DatasetVersion` (§9).

## 5. The LeRobot implementation (Request 3.3)

### Proven v1 mapping

```text
SceneOps observation projection  -> LeRobot "observation.state"
SceneOps action projection       -> LeRobot "action"
SceneOps task                    -> LeRobot per-frame "task" string
```

This mirrors LeRobot's own `hw_to_dataset_features` convention for
non-visual (joint-state) features exactly — not an invented mapping.
`names` is left `None` on both LeRobot features: `ExternalStep` only ever
carries an already-flattened dense vector (never a `FeatureSchema`), so the
writer cannot recover which flattened index came from which SceneOps
channel from that alone — that per-channel offset/kind mapping is only
ever available from `ExternalExportReport.feature_schema`, returned to the
caller once `export()` completes.

FPS is *derived*, never configured: SceneOps Episodes are fixed-frequency
by construction, so the first non-empty Episode's own step spacing already
carries the dataset's frequency. A non-uniform grid, a non-integer-Hz
spacing, or two Episodes resolving to different frequencies are all
rejected outright (`NonUniformTimelineError`/`NonIntegerFrequencyError`/
`InconsistentFrequencyError`) rather than resampled.

### Semantic-loss classification (`LeRobotDatasetAdapter.semantic_capabilities()`)

| SemanticField | Mapping | Why |
| --- | --- | --- |
| `STEP_ORDERING` | `LOSSLESS` | `add_frame()` called once per step, in export order; an empty Episode is rejected outright rather than reordered/coerced. |
| `OBSERVATION_ACTION_NAMESPACE` | `LOSSLESS` | Kept as two separate LeRobot features (`observation.state` vs `action`) — no shared namespace, no collision. |
| `FEATURE_ORDERING` | `LOSSLESS` | `FeatureProjection`'s declared channel order is preserved unchanged into the flat vector. |
| `TIMESTAMPS` | `LOSSY_EXPLICIT` | LeRobot's per-frame `timestamp` is structurally a *relative*, episode-local value (`frame_index / fps`). Intra-episode spacing survives exactly given the writer's validated uniform grid, but the absolute source-clock anchor (`EpisodeMetadata.source_start_timestamp_us`/`source_clock`) is not represented by any LeRobot field. |
| `EPISODE_IDENTITY` | `LOSSY_EXPLICIT` | LeRobot's `episode_index` is a bare, sequential integer with no native slot for `(episode_id, aligned_artifact_checksum)`. Full traceability still exists — export order is exactly `ExternalExportReport.source_episode_refs`' order — but recovering it requires keeping that report alongside the LeRobot dataset. |
| `SOURCE_REVISION_TRACEABILITY` | `LOSSY_EXPLICIT` | Same reasoning at the dataset level: LeRobot's `info.json` has no field for SceneOps' `dataset_id`/`dataset_version`/`export_id`; `ExternalExportReport.source_dataset_id`/`source_dataset_version`/`source_export_id` is the explicit record. |
| `TASK_OUTCOME_METADATA` | `LOSSY_EXPLICIT` | Task string carries `EpisodeMetadata.task` unchanged (an Episode with `task=None` is rejected, never substituted). `EpisodeOutcome` has no LeRobot-native field at all. |
| `SIGNAL_STATUS` | `UNSUPPORTED` | Already collapsed before this layer ever sees a step — Phase 2's dense projection (`MissingFeaturePolicy.ERROR`) forecloses `RESOLVED`/`INTERPOLATED` distinction unconditionally, independent of target format. Not a LeRobot-specific limitation. |

### Isolated runtime — the current contract, not a temporary workaround

`lerobot` is **not** declared as an optional extra of `sceneops-analytics`
(even though the adapter code lives inside that package, at
`sceneops_analytics.external_adapters.lerobot`). `sceneops-analytics` is a
`[tool.uv.workspace]` member, and uv resolves every declared extra of
every workspace member into one universal `uv.lock` regardless of whether
anything requests it — lerobot 0.4.4's dependency graph needs `numpy>=2`,
which is permanently incompatible with `apps/worker`'s `nuscenes-devkit`
pin (its only `numpy<2`-compatible fallback needs a `matplotlib` release
with no Python 3.11 wheels on PyPI at all). `[tool.uv.conflicts]` was
tried first and does not cleanly fix this — for a third-party package
pulled in through a workspace member that isn't part of the declared
conflict pair (`nuscenes-devkit`, via `apps/worker`), the generated marker
for its degraded fallback still matched "no extra requested at all",
verified by `uv sync --all-packages --group dev --dry-run` silently
downgrading `matplotlib`/`nuscenes-devkit` with zero flags passed.

The shipped, permanent answer: `tools/lerobot-integration/` is a small,
standalone uv project — deliberately **not** a `[tool.uv.workspace]`
member — with its own independent, committed `uv.lock`. It depends on the
*existing* `sceneops-core`/`sceneops-storage`/`sceneops-analytics` code via
editable path sources (`{ path = "../../packages/...", editable = true }`)
plus `lerobot` directly. No adapter code is duplicated there; only the
dependency resolution is isolated. Its lockfile's dependency graph never
includes `apps/worker` or `nuscenes-devkit`, so the conflict structurally
cannot occur inside it.

> **Update (Phase 4/Request 4.6B):** `apps/worker` no longer declares
> `nuscenes-devkit` at all, so the specific `numpy<2` pin described above
> no longer lives in the root workspace's `uv.lock` either — this doesn't
> undo the isolation this section describes, it just means the *original*
> trigger for building it this way is now historical, not a live
> constraint. See
> [External integration runtime](./external-integration-runtime.md) §6 for
> the full current-state explanation and the accurate reason
> `tools/lerobot-integration` stays isolated today.

```bash
make lerobot-sync    # cd tools/lerobot-integration && uv sync --group dev --locked
make lerobot-test    # runs packages/sceneops-analytics/tests/test_lerobot_adapter.py there
```

`import sceneops_analytics` (and `import sceneops_analytics.
external_adapters`) works with `lerobot` completely absent — confirmed by
a dedicated regression test
(`packages/sceneops-analytics/tests/test_package_boundaries.py`'s
`test_no_source_file_outside_lerobot_subpackage_imports_lerobot`/
`test_importing_sceneops_analytics_does_not_pull_in_lerobot`, an AST-scan +
runtime import check, not just a docstring claim). `packages/sceneops-
analytics/tests/test_lerobot_adapter.py` uses `pytest.importorskip
("lerobot")`, so `make test` skips it cleanly (1 skipped) in the normal
workspace venv where `lerobot` is never installed.

This split — one shared workspace lock for the platform, one small
isolated lock for a permanently version-incompatible optional
integration — is the durable runtime contract this phase closes on, not a
placeholder pending a future fix. Phase 4 may containerize it; Phase 4
does not need to re-decide *whether* to isolate it.

## 6. Interoperability verification (Request 3.4)

Real, live, end-to-end, run against actual Postgres + MinIO (`make
local-up`), not mocked:

```text
persistent "interop" fixture (real Postgres + MinIO ArtifactRecords)
        |
        v
SceneOpsDataset.open(...)                    (real infra, never LocalArtifactStore)
        |
        v
LeRobotDatasetAdapter.export(...)            (tools/lerobot-integration, isolated venv)
        |
        v
real LeRobot v3 dataset on disk
        |
        v
official lerobot.datasets.lerobot_dataset.LeRobotDataset reader
        |
        v
golden semantic comparison (sceneops_analytics.testing.interop_dataset)
```

Two Python processes/venvs, deliberately, matching §5's isolation
boundary: `scripts/e2e/e2e_lerobot_resolve.py` runs in the main workspace
venv (needs `sceneops-db` to query the real `ArtifactRecord`), prints the
manifest's real `uri`/checksum; `scripts/e2e/e2e_lerobot_export.py` runs
entirely inside `tools/lerobot-integration`'s isolated venv and does
everything from opening the real `SceneOpsDataset` onward. Orchestrated by
`scripts/e2e/e2e_lerobot_roundtrip.sh`, invoked as `make e2e-lerobot`.

Verified fixture expectations (the deterministic `interop` golden fixture
— `EPISODE_A_REV1`/`EPISODE_A_REV2`/`EPISODE_B`, Request 3.2):

```text
3 exported episodes   (both "ep-a" revisions distinct + 1 "ep-b")
22 total steps        (8 + 8 + 6)
observation dim = 7   (3-vector + 3-vector + 1-scalar)
action dim = 4         (3-vector + 1-scalar)
fps = 10               (10.0 Hz fixed-frequency fixture)
```

LeRobot's relative per-frame timestamp is validated against
`step_index / fps` (the fixed-step spacing the fixture itself defines) —
**never** against SceneOps' absolute `timestamp_us`, which would
misrepresent a relative, episode-local value as lossless (§5's
`TIMESTAMPS -> LOSSY_EXPLICIT` classification). The export report's
`semantic_losses` are checked against the literal `SemanticField`/
`MappingKind` values from §5's table, not a re-derivation of the
classification logic.

The exported LeRobot dataset lands at a fixed, test-owned location —
`data/runs/e2e-lerobot/<repo-id>/` (`data/` is entirely gitignored; the
platform's existing convention for disposable run output, see
`makefiles/cleanup.mk`'s `clean-artifacts`). Only that one directory is
ever removed, and only at the start of a run (`LeRobotDataset.create()`
requires its target not already exist) — never anything broader.

Failure-path behavior was verified live, not just asserted in code: a
wrong manifest checksum, a nonexistent manifest URI, and a deliberately
broken assertion (temporarily forcing an expected episode count to 999)
all produced a clear, single-line `❌ ...` message and a non-zero exit
code — never a silent pass.

## 7. Fixture / bootstrap boundary (`sceneops-e2e-v1` catalog)

```text
core       shared Scene/Episode workflows (pipeline-contracts, dataset-
           ingestion, scenario/episode curation, episode building, ...).
           Real nuScenes-mini source.
interop    deterministic interoperability source, built once from hand-
           written golden AlignedEpisode/AlignedEpisodeArtifact data
           (sceneops_analytics.testing.interop_dataset, Request 3.2) —
           not derived from nuScenes ingestion.
raw-log    intentionally isolated (own DatasetVersion) -- non-ground-truth
           scenes would drag down core's aggregate quality readiness if
           shared, a real previously-discovered data-requirement
           conflict, not an oversight.
```

`make e2e-bootstrap-interop` and `make e2e-lerobot` are two different,
composable steps with a clean boundary between them:

```text
make e2e-bootstrap-interop
  -> idempotently creates/reuses + independently verifies the interop
     fixture's LearningDataExportManifest + 3 Parquet tables +
     ArtifactRecords. A *verified canonical prerequisite* -- never
     touches LeRobot, never writes anything export-format-specific.

make e2e-lerobot
  -> calls the exact same ensure_e2e_fixture("interop", ...) contract
     internally (no bootstrap logic re-derived), then exercises the
     *actual external export + official read-back* behavior on top of
     whatever that call already guaranteed is ready.
```

Bootstrapping the `interop` fixture never pre-creates any LeRobot output —
the only thing `_bootstrap_interop`/`ensure_e2e_fixture` ever write is
SceneOps' own canonical Parquet/manifest state; the LeRobot dataset is
produced fresh by `make e2e-lerobot` (or a direct adapter call) every time,
never cached or pre-seeded by the fixture bootstrap.

## 8. Component ownership

| Component | Owns | Package |
| --- | --- | --- |
| `ExternalDatasetRef` | Shared vocabulary for a dataset outside SceneOps' model (import or export) | `sceneops_core.datasets.schemas` |
| `ExternalDatasetAdapter` / `ExternalDatasetWriter` | Framework-neutral export contract, orchestration, semantic-loss bookkeeping | `sceneops_analytics.external_adapters` |
| `LeRobotDatasetAdapter` / `LeRobotDatasetWriter` | Concrete LeRobot v3 mapping/writer, fps derivation, semantic classification | `sceneops_analytics.external_adapters.lerobot` (optional, needs `tools/lerobot-integration`) |
| Interop golden fixture | Deterministic hand-built source data + expected values, shared by unit tests and the persistent E2E bootstrap | `sceneops_analytics.testing.interop_dataset` |
| Persistent E2E fixture bootstrap | Real Postgres/MinIO create-reuse-verify contract for `core`/`interop`/`raw-log` | `scripts/e2e/e2e_fixture_bootstrap.py` |
| LeRobot round-trip E2E | Two-process orchestration (resolve in main venv, export+verify in isolated venv) | `scripts/e2e/e2e_lerobot_{resolve,export}.py`, `e2e_lerobot_roundtrip.sh` |
| Isolated LeRobot environment | Its own `pyproject.toml`/`uv.lock`, editable path sources onto the real package code | `tools/lerobot-integration/` |

## 9. Persisted vs. runtime-only representations

**Persisted** (unchanged from Phase 2 — Phase 3 adds no new `ArtifactKind`,
DB table, or Job/API endpoint):

```text
(all of robot-learning-data.md §6's list, untouched)
```

**Runtime-only** (Phase 3 additions, never written back to ArtifactStore
or Postgres):

```text
ExternalExportConfig / ExternalEpisode / ExternalStep / ExternalExportReport
      -- in-memory contract only, returned directly to export()'s caller
ExternalDatasetRef (LeRobot side)
      -- LeRobotDatasetAdapter.dataset_ref; pure construction, never persisted
real LeRobot v3 dataset directory
      -- written to local disk by LeRobotDatasetWriter; not registered as
         a SceneOps Dataset/DatasetVersion, not given an ArtifactRecord
```

## 10. Current intentional limitations

These are deliberate Phase 3 v1 boundaries, verified against the code —
not correctness failures to fix before closing the phase:

- Numeric scalar/vector interoperability only — the same v1 dense-
  projection scope Phase 2 already established
  (`AlignedValueKind.NUMERIC_SCALAR`/`NUMERIC_VECTOR`); no image/video
  export exists or was attempted.
- `SIGNAL_STATUS` (`RESOLVED` vs `INTERPOLATED`) is unavailable to any
  adapter after Phase 2's dense projection — not something a better
  LeRobot mapping could recover; classified `UNSUPPORTED` for exactly
  that reason (§5).
- No persistent SceneOps record exists for an external export — no
  `ArtifactRecord`, no DB row, no API endpoint. `ExternalExportReport` is
  returned directly to the caller and never persisted by this phase.
- LeRobot output is never a SceneOps `DatasetVersion` — it is described
  only as an `ExternalDatasetRef`, by design (§4/§9).
- The LeRobot runtime is isolated from the main workspace by a real
  structural dependency conflict at the time this phase closed —
  `lerobot`'s `numpy>=2` vs. `apps/worker`'s then-`nuscenes-devkit` pin
  (`numpy<2`), not a temporary inconvenience (§5). **Historical as of
  Phase 4/Request 4.6B**: `apps/worker` no longer depends on
  `nuscenes-devkit` at all, so this specific conflict no longer exists in
  the root workspace lock — see
  [External integration runtime](./external-integration-runtime.md) §6 for
  the current, accurate isolation rationale (general SDK/runtime
  isolation, not an active NumPy conflict).
- `make e2e-lerobot` is optional and intentionally **not** part of
  default `make e2e` — it requires the isolated environment
  (`make lerobot-sync`) as a one-time prerequisite, unlike every script in
  the default suite.
- RLDS is not implemented — `ExternalDatasetAdapter`/`ExternalDatasetWriter`
  are format-neutral and already support a second concrete adapter, but
  none exists yet.
- Integration runtime packaging/containerization was deferred to Phase 4
  at the time this document closed (§11) — now done: see
  [External integration runtime](./external-integration-runtime.md) §6
  (`tools/lerobot-integration` packaged as a container, Request 4.2/4.3).
- The worked identity-model example in §2 (nuScenes -> `core` ->
  LeRobot) is architecturally valid and each half is independently
  tested, but no single E2E chains live nuScenes ingestion directly into
  a LeRobot export today — the verified round-trip (§6) uses the
  separate, deterministic `interop` fixture instead.

## 11. Phase 3 close-out and Phase 4 boundary

```text
Phase 3 -- Dataset Interoperability          COMPLETE

  3.1   External adapter contract (ExternalDatasetAdapter/Writer,
        semantic-loss model)
  3.1A  Write lifecycle refinement (initialize/write_episode/finalize)
  3.2   Deterministic interoperability golden fixture
        (sceneops_analytics.testing.interop_dataset)
  3.2A  E2E fixture catalog v1 (per-workflow derived identities)
  3.2B  E2E fixture catalog v2 (shared core/interop/raw-log identities,
        canonical vs. source identity separation)
  3.2C  Persistent E2E fixture bootstrap (real Postgres/MinIO)
  3.2C.1 Bootstrap hardening (create/reuse/verify contract,
        sceneops-db dependency moved out of sceneops-analytics)
  3.3   LeRobotDatasetAdapter / LeRobotDatasetWriter (concrete
        implementation, v1 numeric mapping, semantic classification)
  3.3A  Dependency isolation (tools/lerobot-integration, independent
        uv.lock, [tool.uv.conflicts] evaluated and rejected)
  3.4   LeRobot round-trip E2E against real persistent infrastructure
        (make e2e-lerobot)
  3.5   Architecture/documentation freeze (this document)
```

Phase 3 ends at "one concrete external adapter, proven end-to-end against
real infrastructure, running in a permanently isolated but fully
reproducible environment." It does not attempt runtime packaging,
containerization, a second (RLDS) adapter, or persistent SceneOps records
for external exports — those are explicitly out of scope, not gaps.

**Phase 4 ("External Integration Runtime") is now COMPLETE** — see
[External integration runtime](./external-integration-runtime.md) for its
full closure document (reference/runtime contract, ownership boundaries,
`IntegrationExecutor`/HTTP transport, final nuScenes and LeRobot
architecture, package layout, intentional limitations, and next-phase
candidates). It did not redesign Phase 3's semantic adapter contracts
(§3/§4), the LeRobot mapping or semantic-loss classification (§5), or
`ExternalDatasetRef`/`Dataset`/`DatasetVersion` identity ownership (§2) —
those remain frozen by this document, unchanged.

## 12. Source-of-truth map

- Shared adapter contract: `packages/sceneops-analytics/sceneops_analytics/external_adapters/{adapter,writer,schemas,enums,errors,validation}.py`
- Concrete LeRobot adapter: `packages/sceneops-analytics/sceneops_analytics/external_adapters/lerobot/`
- Interop golden fixture: `packages/sceneops-analytics/sceneops_analytics/testing/interop_dataset.py`
- Isolated LeRobot environment: `tools/lerobot-integration/` (`pyproject.toml`, `uv.lock`, `README.md`)
- Persistent E2E fixture bootstrap: `scripts/e2e/e2e_fixture_bootstrap.py`, `scripts/e2e/bootstrap_e2e_fixtures.py`
- LeRobot round-trip E2E: `scripts/e2e/e2e_lerobot_resolve.py`, `scripts/e2e/e2e_lerobot_export.py`, `scripts/e2e/e2e_lerobot_roundtrip.sh`
- E2E fixture catalog (bash side): `scripts/e2e/lib.sh`'s `resolve_e2e_fixture`
- Makefile targets: `makefiles/lerobot.mk` (`lerobot-sync`/`lerobot-lock`/`lerobot-test`/`e2e-lerobot`), `makefiles/e2e.mk` (`e2e-bootstrap-interop`)
- `ExternalDatasetRef`: `packages/sceneops-core/sceneops_core/datasets/schemas/external.py`
