# Robot Learning Data Layer (Phase 2)

> Describes Phase 2 as it exists today on `feat/robot-learning-data-layer`.
> Like [overview.md](./overview.md), this is a "what's actually built"
> document, not aspirational — every claim below was checked against the
> code, not against the original request planning documents. Where the two
> disagreed, this doc follows the code (see §7 for the mismatches found).

## 1. Purpose

Phase 1 (Scene/Episode foundation, `v0.2.0-scene-episode-foundation`) gave
SceneOps registered, quality-scored `EpisodeRecord`s built from raw robot
recordings. It stopped at "here is a segmented observation+action window" —
nothing downstream understood *training* semantics: no fixed-frequency
timeline, no dense feature tensors, no notion of a trajectory window.

Phase 2 builds that layer on top of registered Episodes: temporal
alignment, structural validation/profiling, a columnar Parquet export,
revision-level curation, and a native read-side dataset/sampler/consumer-
adapter stack. SceneOps now owns its **own** learning-data representation
end to end — an `AlignedEpisode`, its columnar projection, and the
`SceneOpsDataset`/`SequenceSampler` access layer are first-class SceneOps
concepts, not a thin wrapper around an external format. External training
formats (LeRobot, RLDS, ...) are explicitly **not** part of this canonical
model — see §9 (Phase 3 boundary).

## 2. Final canonical flow

```text
Raw asynchronous robot streams (ROS2 -> MCAP)
        |
        v
EpisodeManifest                          (Phase 1 -- see episode-domain.md)
        |
        v
Temporal Alignment                       (2.1-2.2, pure engine)
        |
        v
AlignedEpisode                           (canonical aligned representation)
        |
        v
Validation / Profiling                   (2.4, structural + descriptive)
        |
        v
Learning Data Export                     (2.5)
        |
        v
learning_episodes.parquet
learning_steps.parquet
learning_signals.parquet
        |
        v
Episode Curation                         (2.6, selection over aligned revisions)
        |
        v
SceneOpsDataset                          (2.7B, read-only Parquet access)
        |
        v
FeatureProjection -> FeatureSchema       (2.7A, dense projection contract)
        |
        v
SequenceSampler                          (2.7C, deterministic window index)
        |
        v
SequenceSample                           (2.7A, framework-neutral dense window)
        |
        v
NumPySequenceSample -> SceneOpsTorchDataset   (2.7D, consumer adapters)
```

Every arrow above is a real, tested code path (see §5 for exact module
locations), not a target architecture.

## 3. Frozen semantic identities

Three distinct identities recur through every Phase 2 layer. They must
never be collapsed into each other:

```text
logical identity        episode_id
                         The task-level Episode this data came from. One
                         episode_id can have many aligned revisions over
                         time (re-alignment with a different config,
                         re-registration from updated source data, ...).

content/revision identity   aligned_artifact_checksum
                         The actual semantic identity of one aligned
                         revision -- a content hash of the persisted
                         AlignedEpisodeArtifact bytes. Two revisions with
                         the same episode_id but different checksums are
                         different data.

producer lineage         artifact_id
                         Which ArtifactRecord/write produced a given blob.
                         Minted fresh on every write, even when content is
                         byte-identical to a previous write. Lineage only
                         -- never used as a stand-in for revision identity
                         anywhere in Phase 2 (see AlignedArtifactRevision,
                         SourceLearningExportRef, EpisodeCurationManifest
                         .decisions -- every one of these keys off checksum,
                         never off artifact_id).
```

For native dataset access, this collapses to one frozen coordinate
(`sceneops_core.episodes.learning.EpisodeRef`, Request 2.7A):

```text
EpisodeRef = episode_id + aligned_artifact_checksum
```

`SceneOpsDataset`/`SequenceSampler` never identify an Episode by
`episode_id` alone. Two `EpisodeRef`s with the same `episode_id` but
different checksums are distinct, independently-indexed data (verified by
dedicated tests at every layer: `test_learning_projection.py`,
`test_learning_dataset.py`, `test_sequence_sampler.py`).

## 4. Frozen data semantics

Future phases (and any Phase 3 external-format adapter) must preserve
these exactly.

### Temporal (2.1-2.2)

- Alignment always targets an explicit fixed-frequency timeline
  (`TemporalAlignmentConfig.target_frequency_hz`) -- no implicit/inferred
  frequency.
- Every resolved signal carries exact source timestamp provenance
  (`source_timestamp_us`/`time_delta_us` for direct association,
  `source_before_timestamp_us`/`source_after_timestamp_us`/
  `interpolation_ratio` for interpolation).
- Association policy (`exact`/`nearest`/`previous`/`linear_interpolation`)
  is always explicit per channel, never inferred.
- No hidden extrapolation or backfill -- v1's policy vocabulary
  deliberately excludes both.

### Signal presence (2.2, 2.5A, 2.7A)

- **ABSENT** = no `learning_signals` row / no dict entry for that channel
  at that step. A channel the Episode never declared.
- **MISSING** = the channel *is* declared, but `AlignedSignalStatus.MISSING`
  at that step (an explicit row/entry with `value=None`).
- **RESOLVED** / **INTERPOLATED** are both usable by dense projection --
  interpolation quality is never re-litigated at the projection layer.
- ABSENT and MISSING are always distinguishable, at every layer down to
  the columnar export and back (`FeatureAbsentError` vs
  `FeatureMissingError` in `sceneops_core.episodes.learning`).

### Feature projection (2.7A)

- `FeatureProjection.observation_channels`/`action_channels` are ordered
  lists; that order directly determines dense output ordering and is
  **never** auto-sorted.
- Dense v1 supports numeric scalar/vector values only --
  orientation/reference values raise `UnsupportedFeatureKindError`.
- No implicit padding or truncation on a shape mismatch --
  `FeatureShapeMismatchError` instead.
- `MissingFeaturePolicy.ERROR` is the only implemented policy; a required
  ABSENT/MISSING feature is a hard error, never silently dropped.

### Sequence (2.7A, 2.7C)

- A `SequenceSample`/window always has a fixed `horizon` and never crosses
  an Episode boundary (`start_step + horizon <= step_count`, enforced by
  `validate_sequence_bounds`).
- `SequenceSampler` indexing is fully deterministic: `dataset.episodes()`
  order, then increasing `start_step` -- never shuffled by the sampler
  itself.
- One `SequenceSampler` instance requires one compatible dense
  `FeatureSchema` across every Episode it draws from (checked once, at
  construction) -- `SamplerSchemaMismatchError` otherwise.

### Curation (2.6)

- Descriptive facts (`CurationCandidateFacts`, recomputed fresh from the
  validator/profiler) and selection policy (`CurationPolicy`) stay
  separate types -- a decision is always "facts + policy -> decision",
  never a policy that also computes its own facts.
- Curation never mutates source data -- `EpisodeCurationManifest` is a
  selection layer over an existing, immutable `LearningDataExportManifest`
  snapshot; it only ever narrows which `aligned_artifact_checksum`s a
  downstream reader sees.
- Not to be confused with the pre-existing, unrelated **scenario**
  curation (`mine_scenarios`/`score_scenario_readiness`, see
  [Quality and run records](./quality-and-runs.md) §4) -- similar name,
  different domain (scenario mining over Scenes, not Episode-revision
  selection).

## 5. Component responsibilities and package ownership

| Component | Owns | Package |
| --- | --- | --- |
| `EpisodeManifest` | Raw observation/action frames from ingestion (Phase 1) | `sceneops_core.episodes.schemas` |
| Temporal Alignment | Pure `EpisodeManifest -> AlignedEpisode` engine; no I/O | `sceneops_core.episodes.alignment` (`engine.py`, `timeline.py`, `policies.py`) |
| `AlignedEpisode` | Canonical aligned representation; one logical `episode_id`, many revisions | `sceneops_core.episodes.alignment.schemas` |
| Revision identity | `AlignedEpisodeArtifact` envelope + `alignment_key`/checksum | `sceneops_core.episodes.alignment.persistence` |
| Validation | Pure structural checks over one `AlignedEpisodeArtifact` | `sceneops_core.episodes.alignment.validation` (`AlignedEpisodeValidator`) |
| Profiling | Pure descriptive statistics (missing/interpolated ratios, sync deltas) | `sceneops_core.episodes.alignment.profiling` (`AlignedEpisodeProfiler`) |
| Learning Data Export | Pure `AlignedEpisodeArtifact -> Polars table` builders | `sceneops_analytics.learning_tables` |
| Curation | Facts + policy -> decision; manifest identity | `sceneops_core.episodes.curation` |
| `SceneOpsDataset` | Read-only Parquet access, curation-aware exposure, lazy table loading | `sceneops_analytics.learning_dataset.dataset` |
| `FeatureProjection`/`FeatureSchema` | Pure dense-projection contract (storage-independent) | `sceneops_core.episodes.learning` |
| `SequenceSampler` | Deterministic fixed-horizon window index over one `SceneOpsDataset` | `sceneops_analytics.learning_dataset.sampler` |
| `NumPySequenceSample` | Framework-neutral consumer conversion | `sceneops_analytics.learning_dataset.numpy_adapter` |
| `SceneOpsTorchDataset` | Thin sync Torch `Dataset` over pre-materialized samples (optional) | `sceneops_analytics.learning_dataset.torch_adapter` |

Worker job handlers (`ALIGN_EPISODE`, `VALIDATE_ALIGNED_EPISODE`,
`PROFILE_ALIGNED_EPISODE`, `EXPORT_LEARNING_DATA`, `CURATE_EPISODES`) live
in `apps/worker/sceneops_worker/jobs/dataset/`, following the same
resolve-verify-parse-then-call-the-pure-class pattern used everywhere else
in this codebase (e.g. `_aligned_episode_resolution.py`,
`_learning_export_resolution.py`).

`SceneOpsDataset`/`SequenceSampler`/the consumer adapters live in
`sceneops-analytics`, not `sceneops-core` -- they need Polars/PyArrow/
NumPy(/optional Torch) and `ArtifactStore` access, which `sceneops-core`
deliberately never depends on. `sceneops-core` stays pure Python +
pydantic only; `sceneops_core.episodes.learning` (2.7A) is pure in-memory
logic with zero storage/network dependency, callable from any layer.

## 6. Persisted vs. runtime-only representations

**Persisted** (ArtifactStore-backed, has an `ArtifactKind`):

```text
EpisodeManifest                    ArtifactKind.EPISODE_MANIFEST
AlignedEpisodeArtifact             ArtifactKind.ALIGNED_EPISODE_MANIFEST
AlignedEpisodeValidationReport      ArtifactKind.ALIGNED_EPISODE_VALIDATION_REPORT
AlignedEpisodeProfile               ArtifactKind.ALIGNED_EPISODE_PROFILE_REPORT
LearningDataExportManifest          ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST
learning_episodes/steps/signals.parquet   ArtifactKind.ANALYTICS_TABLE
EpisodeCurationManifest             ArtifactKind.EPISODE_CURATION_MANIFEST
```

**Runtime/read-side only** (constructed on demand, never written back to
ArtifactStore):

```text
SceneOpsDataset          -- opened per-session from a pinned manifest+checksum
FeatureSchema            -- resolved from Parquet at access time, cached in-memory
StepSample / SequenceRef / SequenceSample   -- projected on demand
NumPySequenceSample      -- produced by explicit to_numpy()/materialize_sequences()
SequenceSampler          -- built per-session from SceneOpsDataset.episodes()
SceneOpsTorchDataset     -- wraps an already-materialized in-memory list
```

This request introduces no new persistence for any runtime-only object
above -- confirmed by the audit; none of 2.7A-2.7D added an `ArtifactKind`,
DB table, or Job/API endpoint.

## 7. Implementation/documentation mismatches found during this audit

Two other architecture docs contained claims that predate Phase 2 and are
now false. Fixed as part of this closure (not new features):

- [reserved-and-limitations.md](./reserved-and-limitations.md) §6 said
  "Episode has no Parquet analytics table" and "Episode has no
  `selectable_for_*` concept" -- both were true before Phase 2 and are
  false now (`learning_*.parquet` + `EpisodeCurationManifest.selected_
  aligned_artifact_checksums` are exactly those two things, scoped to
  aligned revisions rather than raw Episodes).
- [storage-layout.md](./storage-layout.md) §3 said "Episode has no Parquet
  analytics table yet" in the Analytics section -- same fix, plus the
  `learning/`/`curation/` URI scopes were undocumented there entirely.

No other mismatch was found: the 2.7A-2.7D code matches the request
sequence's intended architecture (verified directly against
`sceneops_core/episodes/{alignment,curation,learning,learning_export}` and
`sceneops_analytics/learning_dataset/` during this audit), and none of the
prior five requests' own "frozen contracts" sections have been violated by
a later one.

## 8. Current intentional limitations

These are deliberate v1 boundaries, not correctness bugs:

- `SceneOpsDataset` lazy-loads `learning_steps`/`learning_signals` tables
  (fetched at most once each, on first need) but has no true remote
  predicate pushdown -- `ArtifactStore.read_bytes` has no partial/range-read
  primitive, so a needed table's full bytes are always fetched once
  regardless of backend (local disk or S3/MinIO).
- After that first fetch, the full step/signal table for the whole
  snapshot is held in memory for the life of the `SceneOpsDataset`
  instance; only the row-level *reconstruction into Python objects* is
  scoped per-Episode/per-step.
- Torch integration requires explicit pre-materialization
  (`materialize_sequences`) -- there is no lazy/streaming
  `torch.utils.data.Dataset` yet. `SequenceSampler.get(index)` is async;
  `Dataset.__getitem__` is a hard synchronous contract with no established
  sync read path in `sceneops-storage` to build a lazy bridge on safely.
- Dense feature projection (2.7A) supports numeric scalar/vector only --
  orientation/reference/binary values are not projectable in v1.
- `reference_channel`/`reference_metadata` on a `REFERENCE`-kind
  `AlignedValue` are not recoverable from `learning_signals.parquet` --
  `LEARNING_SIGNALS_SCHEMA` never persisted those two columns (Request
  2.5); only `reference_uri`/`reference_modality` round-trip.
- No missing-value filling/masking/drop policy exists --
  `MissingFeaturePolicy.ERROR` is the only implemented member.
- A LeRobot external dataset format adapter exists (Phase 3); RLDS does
  not -- see §9 and [dataset-interoperability.md](./dataset-interoperability.md).
- `ALIGN_EPISODE`/`VALIDATE_ALIGNED_EPISODE`/`PROFILE_ALIGNED_EPISODE`/
  `EXPORT_LEARNING_DATA`/`CURATE_EPISODES` are registered `JobType`s
  dispatched as standalone Jobs -- none is wired into
  `RAW_LOG_EPISODE_BUILDING_PIPELINE` or any other `PipelineDefinition`
  (the same pipeline-less-by-design pattern already documented for
  `INGEST_ROBOT_STATES`/`EXPORT_ROBOT_ANALYTICS_SNAPSHOT`, see
  [reserved-and-limitations.md](./reserved-and-limitations.md) §1).
- No dedicated API domain exists for alignment/learning-data/curation --
  `apps/api/app/domains/episodes/` only covers the Phase 1 raw-Episode
  surface (build/register/validate/profile/quality). Phase 2 jobs are
  reachable only through the generic Jobs API, not resource-specific
  routes.

## 9. Phase 2 close-out and Phase 3 boundary

```text
Phase 2 -- Robot Learning Data Layer        COMPLETE

  2.1  Temporal Contract / Alignment Config
  2.2  Temporal Alignment Engine
  2.3  Alignment Artifact & Revision Identity
  2.4  Validation / Profiling
  2.5  Columnar Learning Data
  2.6  Episode Curation
  2.7  Native Learning Dataset
       2.7A  Contract (EpisodeRef/FeatureProjection/StepSample/SequenceSample)
       2.7B  SceneOpsDataset
       2.7C  Sequence Sampling
       2.7D  Training Consumer Adapter
```

Phase 2 ends at the native training-consumer layer
(`NumPySequenceSample`/`SceneOpsTorchDataset`). External ecosystem
integration was explicitly out of scope for Phase 2 and moved to Phase 3,
now complete — see
[Dataset interoperability](./dataset-interoperability.md) for the full,
verified architecture (`ExternalDatasetAdapter`/`ExternalDatasetWriter`
contract, the concrete LeRobot adapter, its isolated runtime, and the
real Postgres/MinIO round-trip E2E).

```text
SceneOpsDataset
      |
      +-- Training path (Phase 2, complete)
      |     SequenceSampler -> NumPy / Torch
      |
      +-- Interoperability path (Phase 3, complete)
            ExternalDatasetAdapter -> ExternalDatasetWriter
              -> LeRobot (implemented, Request 3.3)
              -> RLDS (not implemented -- see dataset-interoperability.md §10)
```

Nothing in Phase 2 blocked this: `SceneOpsDataset.get_step()`/
`get_window()` and the 2.7A pure projection functions turned out to be
exactly the seam the Phase 3 external-format adapter reads through,
without any change to `SceneOpsDataset`/`SequenceSampler` themselves.

## 10. Source-of-truth map

- Alignment engine + persistence: `packages/sceneops-core/sceneops_core/episodes/alignment/`
- Curation: `packages/sceneops-core/sceneops_core/episodes/curation/`
- Native dataset contract (2.7A): `packages/sceneops-core/sceneops_core/episodes/learning/`
- Columnar export schemas: `packages/sceneops-analytics/sceneops_analytics/learning_tables.py`
- `SceneOpsDataset`/`SequenceSampler`/consumer adapters: `packages/sceneops-analytics/sceneops_analytics/learning_dataset/`
- Worker job handlers: `apps/worker/sceneops_worker/jobs/dataset/{align_episode,validate_aligned_episode,profile_aligned_episode,export_learning_data,curate_episodes}.py`
- Job type registration: `packages/sceneops-core/sceneops_core/jobs/schemas/{enums,registry}.py`, `apps/worker/sceneops_worker/jobs/registry.py`
