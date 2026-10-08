# Dataset Interoperability

> Describes the external-format interoperability layer as implemented: the
> framework-neutral adapter contract over `SceneOpsDataset` and its one concrete
> implementation, `SceneOpsDataset -> LeRobot`. Every claim was checked against the
> code and against a real run of `make e2e-episode-learning` on Postgres / MinIO.
> Like [robot-learning-data.md](./robot-learning-data.md), it describes what is
> built.

## 1. Purpose

SceneOps' own learning-data representation (`AlignedEpisode`, `SceneOpsDataset`,
`FeatureSchema`, ...; see [Robot learning data layer](./robot-learning-data.md)) is
canonical. This layer adds a *projection* out of it into an external format through
a framework-neutral external-adapter contract over `SceneOpsDataset`, with one
concrete implementation, `SceneOpsDataset -> LeRobot`, and — symmetrically — a shared
vocabulary (`ExternalDatasetRef`) for describing a dataset that lives outside
SceneOps' model at all, on either the import or export side.

## 2. Identity model

Three identities recur through this layer and must never be collapsed into
each other:

```text
ExternalDatasetRef
  A dataset representation OUTSIDE SceneOps' canonical model. Used for
  both an import source and an export target — there is deliberately one
  type, not a separate SourceDatasetRef/ExportDatasetRef pair. Import vs.
  export is a property of the *operation* that uses it, never of the
  ref's own shape.
  format / format_version / uri (+ optional external_name /
  external_revision / checksum); unknown fields rejected
  format is a canonical open identifier ([a-z0-9][a-z0-9._-]*, at most 64
  characters, e.g. "nuscenes", "lerobot"). It is validated, never
  normalized: it becomes part of canonical identity, so aliases are
  resolved by the integration before a ref is built (ADR-007 §27.6).
  uri is location only, never identity.
  sceneops_core.integration_runtime.ExternalDatasetRef

Dataset / DatasetVersion
  SceneOps' OWN canonical identity only. Never constrained by what an
  external format's SDK happens to require.
  dataset_id / version
  sceneops_core.datasets.schemas (DB-backed, see data-model.md §2)

source version (e.g. nuScenes "v1.0-mini")
  The external SOURCE dataset's own version, read only by the
  dataset-acquisition tool that converts it into a recording. Never a
  SceneOps DatasetVersion and never canonical identity.
```

Worked example (the general shape this identity model supports — real
nuScenes data acquired as a recording and canonicalized into SceneOps,
followed by a LeRobot export of that same canonical DatasetVersion):

```text
nuScenes v1.0-mini                          (external source, read by the acquisition tool)
        | dataset-acquisition -> RobotRun -> canonical units
        v
SceneOps test-e2e-core / test-v1            (DatasetVersion, SceneOps-canonical)
        | LeRobotDatasetAdapter.export(...)
        v
LeRobot v3                                  (ExternalDatasetRef, export target)
```

This chain is exercised end to end by `make e2e-episode-learning` (§6): a
recording acquired from nuScenes, canonical Episodes, AlignedEpisodes, a pinned
learning export, a LeRobot export of that export, and a frame-by-frame
comparison read back through LeRobot's own reader.

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
SceneOpsDataset
      |
      v
FeatureProjection                            (the native projection contract,
      |                                        reused unchanged -- channel order,
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

`sceneops_analytics.external_adapters` is the shared,
framework-neutral contract; `sceneops_analytics.external_adapters.lerobot`
is the one concrete implementation of it that exists today.

### Interoperability path vs. training path

```text
SceneOpsDataset
      |
      +-- Training path
      |     SequenceSampler -> NumPy / Torch
      |     fixed-horizon, overlapping, stride-based windows;
      |     shuffled/sampled by the training consumer
      |
      +-- Interoperability path
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

## 4. Adapter / writer contract (frozen)

- `EpisodeRef = episode_id + aligned_artifact_checksum` (the native
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
  the native `FeatureProjection` reused unchanged) — channel order directly
  determines dense output order, never auto-sorted.
- One export requires one compatible dense `FeatureSchema` across every
  Episode it writes (checked once per Episode against the first resolved
  schema) — `ExternalFeatureSchemaMismatchError` otherwise, mirroring
  `SequenceSampler`'s own `SamplerSchemaMismatchError`.
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

## 5. The LeRobot implementation

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
| `SIGNAL_STATUS` | `UNSUPPORTED` | Already collapsed before this layer ever sees a step — the native dense projection (`MissingFeaturePolicy.ERROR`) forecloses `RESOLVED`/`INTERPOLATED` distinction unconditionally, independent of target format. Not a LeRobot-specific limitation. |

### Isolated runtime

`lerobot` is **not** declared as an optional extra of `sceneops-analytics`, even
though the adapter code lives inside that package at
`sceneops_analytics.external_adapters.lerobot`. `sceneops-analytics` is a
`[tool.uv.workspace]` member, and uv resolves every declared extra of every workspace
member into the one universal `uv.lock`, so a declared `lerobot` extra would pull the
LeRobot dependency graph (it needs `numpy>=2`) into the platform lock.

`tools/lerobot-integration/` is instead a standalone uv project, deliberately **not**
a workspace member, with its own committed `uv.lock`. It depends on the existing
`sceneops-core` / `sceneops-storage` / `sceneops-analytics` code through editable
path sources plus `lerobot` directly; no adapter code is duplicated there, only the
dependency resolution is isolated. See
[External integration runtime](./external-integration-runtime.md) §3.

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

This split — one shared workspace lock for the platform, one small isolated lock for
the optional integration — is the runtime contract; the isolated project is also
packaged as the `lerobot-integration` image (`compose/lerobot.yaml`).

## 6. Interoperability verification

The round trip is the last stage of `make e2e-episode-learning`, run on a real
learning export of canonical Episodes (never a hand-built fixture):

```text
episode_learning_data_building pipeline      Episodes -> AlignedEpisodes -> pinned export
        |
        v
lerobot_build_request.py  (worker image)     IntegrationRequest: the export manifest's
        |                                    uri + checksum, the projection, the target
        v
lerobot-integration container                no database; ArtifactStore settings from the
        |                                    environment; operation=EXPORT, format=lerobot
        v
real LeRobot v3 dataset at external_ref.uri
        |
        v
lerobot_verify_export.py  (same image)       official lerobot LeRobotDataset reader
```

The projection is the set of numeric observation and action channels that are
resolved at every step of every episode (`verify_learning_export.py` derives it
from the export's `learning_signals` shards), because v1 export is dense numeric
only (§10). The verification claims:

- the container's `IntegrationResult` names the requested target and canonical
  dataset, and duplicates nothing as an `ArtifactRef`;
- its `ExternalExportReport` counts exactly the export's episodes and steps, in
  the export's canonical `(episode_id, aligned_artifact_checksum)` order;
- read back with `LeRobotDataset`, `total_episodes`, `len(dataset)` and the
  `observation.state` / `action` feature shapes equal the export's, and every
  frame equals the dense window `SceneOpsDataset` reads from the same export for
  the same projection (float32-rounded), with episode-local `frame_index`.

LeRobot's relative per-frame timestamp is never compared against SceneOps'
absolute `timestamp_us`, which would misrepresent a relative, episode-local value
as lossless (§5's `TIMESTAMPS -> LOSSY_EXPLICIT` classification). A second run
against the populated target must fail with "already exists", write no result
and leave the target untouched: the runtime never deletes a caller-owned target.

The LeRobot dataset lands under `data/runs/e2e-lerobot/<baseline-id>/`
(gitignored, removed by the journey on exit); only that directory is ever
removed.

## 7. Test fixtures

`sceneops_analytics.testing.interop_dataset` is a deterministic, in-memory
golden learning export (`EPISODE_A_REV1` / `EPISODE_A_REV2` / `EPISODE_B`) built
from the real learning-data contracts and written through the real
`AnalyticsTableWriter` to a local ArtifactStore. It backs the adapter and
container-entrypoint unit tests (`make lerobot-test`) with fully known values; it
is test-support code, never imported by production code, and nothing persists it
into PostgreSQL or MinIO.

## 8. Component ownership

| Component | Owns | Package |
| --- | --- | --- |
| `ExternalDatasetRef` | Shared vocabulary for a dataset outside SceneOps' model (import or export) | `sceneops_core.datasets.schemas` |
| `ExternalDatasetAdapter` / `ExternalDatasetWriter` | Framework-neutral export contract, orchestration, semantic-loss bookkeeping | `sceneops_analytics.external_adapters` |
| `LeRobotDatasetAdapter` / `LeRobotDatasetWriter` | Concrete LeRobot v3 mapping/writer, fps derivation, semantic classification | `sceneops_analytics.external_adapters.lerobot` (optional, needs `tools/lerobot-integration`) |
| Interop golden fixture | Deterministic hand-built source data + expected values for the adapter / entrypoint unit tests | `sceneops_analytics.testing.interop_dataset` |
| LeRobot round trip | Request built in the worker image, export and read-back in the isolated container image, on a real pinned export | `tools/e2e/lerobot_{build_request,verify_export}.py`, `compose/lerobot.yaml`, `make e2e-episode-learning` |
| Isolated LeRobot environment | Its own `pyproject.toml`/`uv.lock`, editable path sources onto the real package code | `tools/lerobot-integration/` |

## 9. Persisted vs. runtime-only representations

**Persisted** (this layer adds no new `ArtifactKind`,
DB table, or Job/API endpoint):

```text
(all of robot-learning-data.md §6's list, untouched)
```

**Runtime-only** (never written back to ArtifactStore
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

## 10. Current limitations

Deliberate v1 boundaries, verified against the code:

- Numeric scalar / vector interoperability only — the dense-projection scope of
  `AlignedValueKind.NUMERIC_SCALAR` / `NUMERIC_VECTOR`; no image / video export.
- `SIGNAL_STATUS` (`RESOLVED` vs `INTERPOLATED`) is unavailable to any adapter after
  the dense projection, so the LeRobot mapping classifies it `UNSUPPORTED` (§5).
- No persistent SceneOps record exists for an external export — no `ArtifactRecord`,
  no DB row, no API endpoint. `ExternalExportReport` is returned to the caller and
  never persisted.
- LeRobot output is never a SceneOps `DatasetVersion`; it is described only as an
  `ExternalDatasetRef`, by design (§4/§9).
- The LeRobot runtime is isolated in `tools/lerobot-integration/` (own uv project and
  lock) as SDK / runtime isolation; see
  [External integration runtime](./external-integration-runtime.md) §3.
- The LeRobot round trip needs the LeRobot image (`make lerobot-image`); it is part of
  `make e2e-episode-learning` and therefore of `make e2e-cleanroom`.
- LeRobot is the only adapter. `ExternalDatasetAdapter` / `ExternalDatasetWriter` are
  format-neutral, but no second format is implemented.

## 12. Source-of-truth map

- Shared adapter contract: `packages/sceneops-analytics/sceneops_analytics/external_adapters/{adapter,writer,schemas,enums,errors,validation}.py`
- Concrete LeRobot adapter: `packages/sceneops-analytics/sceneops_analytics/external_adapters/lerobot/`
- Interop golden fixture: `packages/sceneops-analytics/sceneops_analytics/testing/interop_dataset.py`
- Isolated LeRobot environment: `tools/lerobot-integration/` (`pyproject.toml`, `uv.lock`, `README.md`)
- LeRobot round trip: `tools/e2e/e2e_episode_learning.sh`, `tools/e2e/lerobot_build_request.py`, `tools/e2e/lerobot_verify_export.py`, `compose/lerobot.yaml`
- Makefile targets: `makefiles/lerobot.mk` (`lerobot-sync`/`lerobot-lock`/`lerobot-test`/`lerobot-image`), `makefiles/e2e.mk` (`e2e-episode-learning`)
- `ExternalDatasetRef`: `packages/sceneops-core/sceneops_core/integration_runtime/external.py`
