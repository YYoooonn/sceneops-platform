# Scene analytics schema v2 deployment acceptance (P5.2B)

> Point-in-time record (2026-11-02). Scene analytics schema v2 on rebuilt API and Celery workers,
> across a real deployment swap on a disposable runtime. Current contracts:
> [ADR-020](../adr/020-scene-analytics-schema-v2.md),
> [scene-domain.md](../architecture/scene-domain.md),
> [storage-layout.md](../architecture/storage-layout.md),
> [limitations.md](../architecture/limitations.md).

## A. Environment

| Item | Value |
| --- | --- |
| Branch / HEAD | `refactor/canonical-domain-boundaries` @ `9a5043d`; the P5.2B changes are uncommitted |
| Docker VM free | 34 GB (94 GB filesystem) |
| Runtime | compose project `sceneops-test`, database `sceneops_test`, bucket `sceneops-test`, its own Redis, on the shared PostgreSQL and MinIO servers |
| Runtime versions | polars 1.43.2 on the host; the images' own locked versions on the workers |

| Image | ID | Code |
| --- | --- | --- |
| `worker:p5-2a-acceptance`, `api:p5-2a-acceptance` | `87ba78b7fbd8`, `8962e3b2b127` | Scene analytics `v1`: unstamped, no temporal context |
| `worker:p5-2b-acceptance`, `api:p5-2b-acceptance` | `d8aa6101bd9f`, `2df9bf672bc0` | this working tree: Scene analytics `v2` |
| `worker:local`, `api:local` | `3c76f695c1c4`, `3acb88a6eaec` | run the reference stack; not rebuilt, restarted or used |

The containers were checked to run the named images after the swap, and `tables.py`, `writer.py`,
`export_analytics_snapshot.py`, `executions/key.py` and `scenes/analytics.py` were hashed inside the
running worker, and where present the API, against the working tree.

## B. Migration across a deployment swap

One disposable runtime, in the order a real upgrade would see. A one-off script drove it through
`disposable_env.py run --runtime`; it is not in the repository because it needs the old images.

```text
Phase A   p5-2a API + workers
          Recording Import of scene-0061 (baseline), publish, register, build and register the
          nuScenes scene-0061 slice; compose both Scenes into one DatasetVersion;
          EXPORT_ANALYTICS_SNAPSHOT: a real v1 snapshot
Swap      `compose up -d`: same database, bucket and Redis
Phase B   this tree's API + workers: the checks below
```

**60 of 60 checks held**, on two complete runs from a fresh database and bucket, the second on images
of the final tree. The first run's images differ from it by `ruff format` only.

### Job identity

| Request after the swap | Result |
| --- | --- |
| Unforced, identical to phase A's | **new Job**, succeeded on a `celery:` worker; key `export_analytics_snapshot:4010a763…` → `f2e48d76…`, both identical across the two runs |
| Repeated | the same Job |
| `force: true` | new Job, same URIs; no object added |

### Snapshot contract

| Requirement | Observed |
| --- | --- |
| Real v1 files | no footer stamp, no context columns |
| Old objects | v1 tables byte-identical at the end; the only objects added are the three v2 tables |
| Distinct identity | v1 and v2 tables at different keys, each with its own `ANALYTICS_TABLE` record (6 records) |
| Reproducibility | the v2 tables' checksums, so their keys, are identical across the two runs from a fresh database and bucket |
| v2 footers | `scene_analytics/v2/<table>` in each; the record's metadata names the same |
| Row values | 2 / 3,569 / 39 rows (scenes / observations / keyframes) of the real v1 snapshot equal the v2 rows on the v1 columns, row order included |
| Added columns | `scenes`: `source_kind`, `content_id`, `window_clock_origin`, `window_clock_scope`; the other two: `clock_origin`, `clock_scope` |
| Restored instance | equal to `manifest.clock_instance(clock)` of each row's own canonical manifest, in all three tables |
| Provenance | equal to the revision record: `robot_run_id` alone for recording, `content_id` alone for content |

| Scene | `window_clock` | origin | scope | observations | keyframes |
| --- | --- | --- | --- | --- | --- |
| nuScenes scene-0061 | `nuscenes.timestamp` | `unix_epoch` | `[]` | 2,963 | 39 |
| Recording scene-0061 | `sensor.header_stamp` | `source` | `[recording, run-ref-nuscenes-mini-full-10-scene-0061]` | 606 | 0 |

`require_comparable` refuses the two windows. The Recording Scene has no keyframes, so a keyframe's
instance on real data is shown for the nuScenes Scene only; recording keyframes are covered in unit
and integration tests.

### Refusals

| Reader, on the real v1 files | Result |
| --- | --- |
| `read_scene_table_bytes`, each table | `UnsupportedSceneAnalyticsSchemaError` naming the missing stamp |
| `query_scene_analytics` | refused |
| `query_parquet` | refused, pointing at `query_scene_analytics` |
| A real v2 table restamped `scene_analytics/v9/keyframes` | refused |

`query_scene_analytics` read the real v2 snapshot, joining observations to scenes.

### Canonical stability

The 37 JSON objects from before the swap (canonical manifests among them) are byte-identical at the
end, and the DatasetVersion and its Scene members are unchanged.

### PipelineRun identity

`GET /pipelines/definitions` names no analytics export task: the snapshot is only ever a direct Job.

| Request after the swap | Result |
| --- | --- |
| Identical unforced `snapshot_scene_building` | **the old phase A run** |
| `force: true` | new run, succeeded |
| Identical unforced again | the forced run |
| The snapshot | not touched by either: a PipelineRun does not produce one |

So the limitation stands, and it does not reach analytics.

## C. Regression

| Command | Result |
| --- | --- |
| In-process upgrade test on real PostgreSQL and MinIO (`test_scene_analytics_upgrade_integration.py`) | 6 passed |
| `make test` | 3,515 passed, 160 skipped, 0 failed across 21 suites; the skips are `*_integration.py` modules, which need the disposable environment |
| `make test-integration` | 253 + 148 + 6 passed, a skip being a failure there |
| `TEST_WORKER_IMAGE=… TEST_API_IMAGE=… make test-infrastructure SUITE=mixed-sources` (final images) | 13 passed in 326 s, with the export read back from MinIO through the supported reader |
| The same, `SUITE=nuscenes-scenes` (images before `ruff format`) | 9 passed |
| `make reference-contract-verify` | `violations: []` |
| `make reference-verify DEEP=1` | 140,446 checks, 0 violations, 30 / 30 negative controls detected |
| `make reference-expectations` (no `WRITE`) | exit 0; no expectation file changed |

The reference commands read the reference stack, which still runs pre-P5.1 images, so they show that
canonical state is unchanged and not that the stack runs this code. No canonical, temporal or
provenance module changed.

The v1 test fixture (`write_v1_scene_tables`) was compared once with the builders and writer of `git
archive HEAD`: byte-identical for all three tables, on a recording Scene with two clocks and a content
Scene.

## D. Not run

- Exports on the reference stack: it needs both images upgraded, and an unforced request there would
  write new objects. Not authorized.
- `make e2e-scene-ml`: it does not run the analytics export.
- A `query_scene_analytics` measurement at scale: the largest table read here has 3,569 rows.
