# Storage Layout

> Based on `packages/sceneops-storage`, `packages/sceneops-core/sceneops_core/config.py`.

## 1. ArtifactStore interface

`sceneops_core.artifacts.contracts.ArtifactStore` Protocol:

```python
join_uri(root, *parts) -> ArtifactUri
exists(uri) -> bool
read_json(uri) / write_json(uri, payload)
read_bytes(uri) / write_bytes(uri, data)
list_json(uri) -> list[ArtifactUri]
delete_prefix(uri) -> None
public_url(uri) -> str
```

JSON and binary reads/writes are separate methods; deletion is
prefix-based (`delete_prefix`, not a single-file `delete`). Every method is
`async` — object-storage calls are network calls.

## 2. Implementations

```text
ArtifactStore
+-- LocalArtifactStore   packages/sceneops-storage/sceneops_storage/backends/local.py
+-- S3ArtifactStore      packages/sceneops-storage/sceneops_storage/backends/s3.py
```

- `LocalArtifactStore`: treats `file://` or scheme-less paths as
  `pathlib.Path`. JSON is written with `json.dump(indent=2)`; directories
  are auto-created (`mkdir(parents=True, exist_ok=True)`).
- `S3ArtifactStore`: `boto3`-based, `s3://<bucket>/<key>` URIs. Setting
  `endpoint_url` switches to path-style addressing, which is how the same
  class also serves MinIO. Every call is wrapped in `asyncio.to_thread`
  around the underlying synchronous boto3 call.
- `create_artifact_store(settings)` picks the implementation from
  `ArtifactBackend` (`LOCAL`/`S3`/`MINIO` — `MINIO` also maps to
  `S3ArtifactStore`).

Both implementations share the same `join_uri()`
(`sceneops_storage.uri.join_uri`), so switching backends never requires
changing call-site URI-composition code — see
[ADR-002](../adr/002-object-storage-for-assets.md).

## 3. Current URI layout

From `.env.example` / `ArtifactSettings` (`sceneops_core/config.py`):

```text
{ARTIFACT_ROOT_URI}/
  datasets/     ArtifactSettings.dataset_prefix
  runs/         ArtifactSettings.run_prefix
  models/       ArtifactSettings.model_prefix
  analytical/   ArtifactSettings.analytics_prefix   (Parquet analytics layer)
  robot_runs/   ArtifactSettings.robot_run_prefix   (published robot recordings)
  observation_payloads/   ArtifactSettings.observation_payload_prefix   (canonical payloads)
```

Local: `/data/artifacts/{datasets,runs,models,analytical,robot_runs}/...`
S3/MinIO: `s3://sceneops/artifacts/{datasets,runs,models,analytical,robot_runs}/...`

### Published robot recordings: write-once per `run_id`

The database-free Recording Publisher (`sceneops_integrations.recording`)
writes two objects per run under the publication root (default
`{ARTIFACT_ROOT_URI}/robot_runs`):

```text
{robot_run_root}/{run_id}/recording.mcap             the finalized MCAP
{robot_run_root}/{run_id}/robot_run_manifest.json    canonical RobotRunManifest v1, written last
```

Both keys are write-once: an existing object with identical bytes is reused
and different bytes are a hard conflict, never an overwrite. The manifest's
`recording.uri` and the registered ArtifactRecords point at these exact
URIs; `REGISTER_ROBOT_RUN` never moves or rewrites them. See
[Robot data ingestion](../workflows/robot-run-and-mcap.md) §3.2.

### Scene manifests: write-once, checksum-qualified

A canonical SceneManifest revision lives at

```text
{dataset_root}/{dataset_id}/versions/{version}/scenes/{scene_id}/manifest-{sha256}.json
```

(`SceneArtifactStore.publish_canonical_manifest`). The key embeds the
checksum of the canonical bytes, so every revision of a Scene coexists and
no key is ever overwritten: re-publishing identical bytes is a no-op and a
key that already holds different bytes is a conflict. Superseded revisions
stay for lineage; there is no garbage collection. Readers never list this
prefix to find "the latest" manifest — they follow a pinned
`manifest_artifact_id` / checksum (see [Scene domain](./scene-domain.md)).

Canonical EpisodeManifest revisions follow the same rule at

```text
{dataset_root}/{dataset_id}/versions/{version}/episodes/{episode_id}/manifest-{sha256}.json
```

(`EpisodeArtifactStore.publish_canonical_manifest`); derived aligned-episode
artifacts sit beside them under `episodes/{episode_id}/aligned/`.

Observation payloads are not addressed by readers through a key
convention: a manifest references each payload by its
`OBSERVATION_PAYLOAD` ArtifactRecord id, and the record's `uri` is the only
place its location is stored. Both recording builders (Scene and Episode)
write them write-once, through one shared store, at

```text
{ARTIFACT_ROOT_URI}/observation_payloads/{robot_run_id}/{artifact_id}
```

(`ArtifactSettings.observation_payload_prefix`). The artifact id is
deterministic per recording message and extraction, so identical bytes at
an existing key are reused and different bytes are a conflict; a Scene and
an Episode build of the same RobotRun share every payload they both extract.

### Analytics (Parquet)

`analytical/{dataset_id}/{dataset_version}/{table_name}.parquet` —
`scenes`/`observations`/`keyframes`/`annotations`, written by the
`export_analytics_snapshot` job via `sceneops-analytics`'
`AnalyticsTableWriter`. Re-running overwrites the same URI (a derived
projection, rebuilt from canonical Scenes).

Robot data is a separate scope from Dataset, so the same writer uses a
second path scheme: `analytical/robot_runs/{robot_run_id}/{table_name}.parquet`
(`robot_telemetry`/`missions`, written by `export_robot_analytics_snapshot`
— see [Robot data ingestion](../workflows/robot-run-and-mcap.md) §4).

The Parquet analytics layer is implemented and query-verified end to end —
`sceneops_analytics.query_parquet()` runs real DuckDB SQL against local
Parquet files (including cross-table joins). MinIO/S3-stored Parquet files
can't be queried directly without DuckDB's httpfs/S3 extension (not wired
up); the supported path downloads to local disk first, then queries.

`export_analytics_snapshot` only covers Scene-domain tables — Episode has
no equivalent flat, overwrite-in-place export. Aligned Episode revisions
do have their own Parquet export under a different scheme; see below.

### Learning data & curation (Phase 2)

Scoped by `export_id`/`curation_id` rather than overwriting a single
per-dataset-version URI the way `export_analytics_snapshot` does —
multiple learning-data export snapshots and curation runs must coexist per
`DatasetVersion`, each identified by its own deterministic id
(`learning_data_export_id`/`episode_curation_id`):

```text
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/learning_episodes.parquet
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/{table_name}/shard-{shard_index:05d}.parquet
{dataset_id}/{dataset_version}/learning/{export_id[:16]}/manifest.json
{dataset_id}/{dataset_version}/curation/{curation_id[:16]}/manifest.json
```

`learning_episodes` is always a single file (metadata-scale, never
sharded). `learning_steps`/`learning_signals` are bounded, sharded
Parquet objects in production (Phase 5, `v2-sharded` layout, always
produced by `EXPORT_LEARNING_DATA` since Request 5.2) — the single-file
scheme (`{table_name}.parquet`, no `shard-NNNNN` segment) is legacy,
still used by the frozen golden-fixture/regression path only; see
[Scalable learning data](./scalable-learning-data.md) §4/§11. Written by
`EXPORT_LEARNING_DATA`/`CURATE_EPISODES` via
`AnalyticsTableWriter.write_learning_table`/`write_learning_table_shard`/
`write_learning_export_manifest`/`write_curation_manifest` — see
[Robot learning data layer](./robot-learning-data.md) and
[Scalable learning data](./scalable-learning-data.md).

### Raw source data

Raw dataset input is fully separate, via its own `RawSourceSettings`
(read-only, independent root):

```text
Local: /data/raw/nuscenes
S3:    s3://sceneops/raw/nuscenes
```

Rosbag/MCAP recordings used for robot ingestion and Episode building follow
the same independent-root pattern: `/data/raw/rosbag/{robot_id}/{run_id}.mcap`
locally (see [Robot data ingestion](../workflows/robot-run-and-mcap.md)).

## 4. What the layout does *not* encode

Storage paths are organized by **resource kind** (`datasets/`, `runs/`,
`models/`, `analytical/`), not by processing stage (there's no `raw/` vs.
`curated/` split at the top level). Whether a `DatasetVersion` is "curated"
is expressed through DB records (membership, run records), not through
where its artifacts physically live.

## 5. Object storage account/infra

Local development uses MinIO (S3-compatible) via `compose/core.yaml`
and `.env.example`'s `MINIO_ROOT_USER`. Switching to real AWS S3 means
clearing `endpoint_url` and swapping credentials — no code change, since
both paths go through `S3ArtifactStore`.

## 6. Known gaps

- `Artifact.checksum`/`Artifact.size_bytes` exist as columns but most
  writers don't populate them — including `export_analytics_snapshot`'s
  and `export_robot_analytics_snapshot`'s `analytics_table` artifacts.
  Whichever consumer eventually needs integrity verification should
  confirm this per write path before relying on it.
