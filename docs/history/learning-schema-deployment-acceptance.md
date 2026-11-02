# Learning Data schema v2 deployment acceptance (P5.2A)

> Point-in-time record (2026-11-02). Learning Data schema v2, enforced versions and
> contract-aware VALIDATE / PROFILE / EXPORT identities on rebuilt API and Celery workers,
> across real deployment swaps on a disposable runtime. Current contracts:
> [ADR-019](../adr/019-learning-data-schema-v2-and-contract-aware-jobs.md),
> [robot-learning-data.md](../architecture/robot-learning-data.md),
> [limitations.md](../architecture/limitations.md).

## A. Environment

| Item | Value |
| --- | --- |
| Branch / HEAD | `refactor/canonical-domain-boundaries` @ `cccef87`; the P5.2A changes are uncommitted |
| Docker VM free | 34 GB after the builds (94 GB filesystem) |
| Runtime | compose project `sceneops-test`, database `sceneops_test`, bucket `sceneops-test`, its own Redis, on the shared PostgreSQL and MinIO servers |
| Runtime versions | polars 1.43.2, pyarrow 25.0.1 on the host; the images' own locked versions on the workers |

| Image | ID | Code |
| --- | --- | --- |
| `worker:local`, `api:local` | `3c76f695c1c4`, `3acb88a6eaec` | pre-P5.1: aligned `v1`, learning `v1`. Run the reference stack, and did not move |
| `worker:p5-1-acceptance`, `api:p5-1-acceptance` | `6492396581db`, `a8ea86416870` | P5.1: aligned `v2`, learning `v1` |
| `worker:p5-2a-acceptance`, `api:p5-2a-acceptance` | `87ba78b7fbd8`, `8962e3b2b127` | this working tree: aligned `v2`, learning `v2` |

The containers were checked to run the named images after each swap, and five modules
(`executions/key.py`, `learning_export/schemas.py`, `learning_dataset/dataset.py`, `writer.py`,
`export_learning_data.py`) were hashed inside the running worker, and where present the API,
against the working tree.

## B. Migration across deployment swaps

One disposable runtime, in the order a real upgrade would see. A one-off script drove it through
`disposable_env.py run --runtime`; it is not in the repository because it needs the old images.

```text
Phase A   pre-P5.1 API + workers
          publish and register pusht, build Episodes 0 and 1 without next.*, compose;
          ALIGN, VALIDATE, PROFILE, EXPORT, a learning PipelineRun
Swap      `compose up -d`: same database, bucket and Redis
Phase A1  P5.1 API + workers
          ALIGN to aligned v2; VALIDATE, PROFILE, EXPORT over it: a real learning v1 export
Swap
Phase B   this tree's API + workers: the checks below
```

**75 of 75 checks held**, on two complete runs from a fresh database and bucket (the first on images
before a one-parameter rename in `dataset.py`, the second on images of the final tree). Three
earlier runs stopped on defects of the script, in its pipeline request and in how it read a task's
result, before reaching their remaining checks.

### Job identity

| Request after the swap | Result |
| --- | --- |
| Unforced VALIDATE of a v1 aligned pin that succeeded in phase A | **new Job, fails** with the schema refusal; the old Job with `valid: true` is not returned |
| Unforced PROFILE of the same pin | new Job, fails with the refusal |
| Unforced VALIDATE and PROFILE of a v2 pin | new Job (key `validate_aligned_episode:48123f2f…` and `profile_aligned_episode:597ac796…` are no longer the answer), succeeded on a `celery:` worker, `valid: true` |
| Repeated | the same Job |
| Unforced EXPORT of the pins exported in phase A1 | new Job (key `export_learning_data:3fa1b020…` is no longer the answer), succeeded on a `celery:` worker |
| Repeated | the same Job |
| `force: true` | new Job, same export id, manifest URI and manifest artifact id; no object added or changed |
| EXPORT over v1 aligned pins | new Job, fails with the refusal |

### Export contract

| Requirement | Observed |
| --- | --- |
| Distinct identity | export id `5de3241f…` (v1) → `211e8f82…` (v2), both identical across the two runs |
| Old objects | all 34 objects from before the upgrade byte-identical at the end, 46 objects in all; no write-once conflict in the worker log |
| Real v1 files | `learning_episodes` without the new columns, no footer stamp |
| v2 footers | every table states its schema, read on MinIO with ranged reads |
| Restored instance | equal to AlignedEpisode v2's and to `manifest.clock_instance(clock)` of the canonical manifest, for both Episodes |
| Lineage | manifest inputs pin the aligned revisions; each names the Episode's manifest artifact |
| Row values | 2 / 279 / 837 rows (episodes / steps / signals) of the real v1 export equal the v2 rows, apart from `export_id`, `clock_origin` and `clock_scope` |
| Frame references | 279 persisted, each with a `video_frame` selector; no frame decoded |

| Episode | `source_clock` | `clock_origin` | `clock_scope` |
| --- | --- | --- | --- |
| pusht 0 | `lerobot.frame_time` | `unit` | `[content, content-0322a8b9…, 0]` |
| pusht 1 | `lerobot.frame_time` | `unit` | `[content, content-0322a8b9…, 1]` |

`require_comparable` refuses the two instances, whose frame times are identical.

### Refusals

| Request | Result |
| --- | --- |
| Incremental export on a real v1 base | Job fails with `IncrementalExportUnsupportedBaseError` naming `'v1'`; no object added |
| Incremental export on a v2 base | succeeded; one shard per table reused; both instances restored |
| CURATE_EPISODES over a real v1 export (forced) | Job fails with the schema refusal |
| Current reader on the real v1 manifest | `UnsupportedLearningDataSchemaError` |

### PipelineRun identity (limitation, not fixed)

| Request after the swap | Result |
| --- | --- |
| Identical unforced | **the old phase A run** |
| `force: true` | new run, succeeded; its export is v2 and converges on the direct export's id and manifest |
| Identical unforced again | the forced run |
| The old run's export, read | refused |

Nothing was forced or migrated on the reference environment.

## C. Regression

| Command | Result |
| --- | --- |
| `TEST_WORKER_IMAGE=… TEST_API_IMAGE=… make test-infrastructure SUITE=lerobot-episodes` (final images) | 11 passed |
| `make reference-contract-verify` | `violations: []` |
| `make reference-verify DEEP=1` | 140,446 checks, 0 violations, 30 / 30 negative controls detected |
| `make reference-expectations` (no `WRITE`) and the verifier selftests | exit 0; no expectation file changed |

These read the reference stack, which still runs the pre-P5.1 images, so they show that canonical
state is unchanged and not that the stack runs this code. No canonical, alignment, temporal or
provenance module changed.

## D. Not run

- `make e2e-episode-learning`: it writes to the reference environment's test-owned Dataset, and a
  useful run needs that stack on new images plus one forced PipelineRun, which were not authorized.
- `make test-infrastructure SUITE=pipelines` and `SUITE=recovery`: their contracts did not change.
  `recovery` runs one learning pipeline in its lost-dispatch cases; the forced run above executes
  that path on the new images.
- The `lerobot-integration` image was not rebuilt. Its entrypoint's refusal is covered by
  `make lerobot-test`, not on a container.
