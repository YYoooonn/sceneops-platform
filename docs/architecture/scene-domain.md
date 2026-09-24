# Scene Domain

A `Scene` is the canonical unit of registered sensor data in SceneOps — one
scene from a driving/robotics dataset, with its samples, frames, GT
annotations, and sensor channels. `SceneRecord` (see
[Data model](./data-model.md) §3) is the source of truth for scene
membership; `DatasetManifest` is a derived snapshot generated from it, not
the other way around.

## 1. Two ways into the Scene domain

```text
DATASET_SCENE_INGESTION pipeline
  ingest_scenes -> register_scene -> validate_scene -> profile_scene
  -> build_scene_index -> build_dataset_manifest

RAW_LOG_SCENE_BUILDING pipeline
  build_scenes -> register_scene -> validate_scene -> profile_scene
  -> build_scene_index -> build_dataset_manifest

SCENE_REGISTRATION pipeline (generated/reconstructed/simulated scenes)
  register_scene -> validate_scene -> profile_scene
```

`ingest_scenes` converts an existing structured dataset (nuScenes mini in
the default local stack) into `SceneManifest`s directly. `build_scenes`
instead starts from a raw sensor log (or, for robot data, a rosbag2/MCAP
file decoded by `RosbagAdapter`) via the `RawLogAdapter` Protocol
(`apps/worker/sceneops_worker/observations/adapters/`), segments it into
scenes, and produces the same `SceneManifest` shape. Both pipelines
converge on the identical `register_scene -> validate_scene -> profile_scene
-> build_scene_index -> build_dataset_manifest` tail — `validate_scene`/
`profile_scene` are reusable quality stages, independent of where the scene
came from.

`SCENE_REGISTRATION` is the same tail without a build/ingest head, for
scenes created by a process outside these two pipelines (e.g. a future
reconstruction job) that already produced a manifest.

## 2. Raw-log storage: scoped by `raw_log_id`

Every raw-log artifact path (`raw_log_manifest_uri`, `raw_frame_index_uri`,
`scene_segments_uri`) is scoped under `.../raw/{raw_log_id}/{filename}.json`
(`ObservationArtifactStore`, `apps/worker/sceneops_worker/observations/artifacts.py`).
This matters because a single `DatasetVersion` can have multiple raw-log
builds run against it over time (e.g. re-ingesting a corrected log, or
building from more than one raw log into the same version) — without the
`raw_log_id` segment, a second build would silently overwrite the first
build's raw-log artifacts at the same URI. Every raw-log source
(`RosbagAdapter` in-process; nuScenes via the isolated integration service,
see [External integration runtime](./external-integration-runtime.md)) and
`SceneBuilder` all pass `raw_log_id` through to these URI builders.

## 3. Artifact ownership: producer owns the record

The Scene domain follows one invariant everywhere: **the pipeline stage
that physically writes an artifact's bytes is the one that creates its
`ArtifactRecord`.** A downstream stage that only *reads* an artifact (via
its URI, passed through `PipelineTaskInputs.refs`) never creates a second
`ArtifactRecord` for the same object.

Concretely: `ingest_scenes`/`build_scenes` write the `SCENE_MANIFEST`
artifact and register it. `register_scene` reads that same manifest URI
(resolved from the upstream `scene_manifest_uris` ref) to upsert the
`SceneRecord` row — it does **not** create its own `SCENE_MANIFEST`
`ArtifactRecord`, since it never wrote that file. This wasn't always true:
`register_scene` used to also insert a duplicate `ArtifactRecord` for the
manifest it only read, producing two rows referencing the same URI with
different `job_id`s. See [Jobs and pipelines](./jobs-and-pipelines.md) §7
for the general form of this invariant, including how Episode applies it.

## 4. SceneStatus lifecycle

```text
CREATED -> BUILT -> VALIDATED | FAILED -> PROFILED
```

`SceneStatus` folds validate/profile *outcomes* into the record's own
`status` field (`validate_scene.py`/`profile_scene.py` write it directly) —
unlike Episode, where status only tracks registration and quality is always
derived live from run records (see [Episode domain](./episode-domain.md)
§4). `VALIDATING`/`PROFILING` (transitional "in progress" states) and
`DEPRECATED` (a soft-delete marker) were removed from the enum: zero write
sites, zero persisted rows, and "in progress" is already covered by
`SceneValidationRunRecord`/`SceneProfileRunRecord`'s own `RunStatus.RUNNING`.

**`BUILT` is intentionally not renamed to `REGISTERED`.** Episode's
equivalent status is literally `REGISTERED`, which reads more consistently
next to Scene's `CREATED`/`BUILT`/`VALIDATED`/`PROFILED`/`FAILED` — but
renaming it would touch every `register_scene`/`validate_scene` write site,
every test asserting on the string value, and API responses already
serializing `"built"` to existing clients, for a purely cosmetic symmetry
gain. This asymmetry between the two domains' status vocabularies is a
documented, deliberate decision, not drift to be "fixed" later.

## 5. Quality and API

Scene readiness and detection-selectability are computed live from
`SceneRecord` + the latest `SceneValidationRunRecord`/`SceneProfileRunRecord`
— see [Quality and run records](./quality-and-runs.md) §2 for the full
readiness/selectability derivation (`apps/api/app/domains/scenes/quality.py`).

Representative routes:

```text
GET /api/v1/scenes
GET /api/v1/scenes/{scene_id}
GET /api/v1/scenes/{scene_id}/quality
GET /api/v1/datasets/{id}/versions/{v}/quality           # dataset-level aggregate
GET /api/v1/datasets/{id}/versions/{v}/scenes/quality     # paginated per-scene quality
GET /api/v1/artifacts?owner_type=scene&owner_id={scene_id}
```

`Scene` has a dedicated `scene_id` column on `ArtifactModel` (unlike
Episode, which is only reachable through the generic `owner_type`/
`owner_id` columns) — see [Jobs and pipelines](./jobs-and-pipelines.md) §7
for that naming asymmetry.
