# Clean bootstrap plan (read-only inventory)

**Status: a plan, not a record of anything done.** Inspected on 2026-11-02 on
`refactor/canonical-domain-boundaries` (base commit `91bd879`, with the P5.3 work uncommitted) with read-only
commands only. Nothing here was executed: no reset, truncate, bucket deletion, prune or image rebuild of
a `:local` tag. The reset needs separate authorization after its scope is reviewed again at the time,
because this inventory ages. Current commands: [test-matrix.md](../development/test-matrix.md) and
[reference-contract.md](../development/reference-contract.md).

## Target

```text
Original source fixtures (data/raw, data/reference, config/reference)
  -> fresh acquisition / native ingestion
  -> canonical Scene v5 / Episode v4
  -> unit revisions / DatasetVersions
  -> AlignedEpisode v2
  -> Learning Data v2 / Scene Analytics v2
  -> full E2E and cross-artifact verification
```

## What is there now

The reference stack still runs `worker:local` (`3c76f695c1c4`) and `api:local` (`3acb88a6eaec`), which
predate `sceneops_core.temporal`, so everything derived it holds is of the old schemas.

| Store | Size | Holds |
| --- | --- | --- |
| Volume `sceneops_minio-data` | 15.15 GB | bucket `sceneops`, 13.7 GB, 12,875 objects, all under `artifacts/`; plus the transient `sceneops-test` bucket while a suite runs |
| Volume `sceneops_postgres-data` | 90.66 MB | databases `sceneops` (21 MB), `postgres`, the transient `sceneops_test`; schema `public`, Alembic head `a9d4e7b2c6f3` |
| Volume `sceneops_kafka-data` | 1.328 GB | the reference broker's log |
| Volume `sceneops_redis-data` | 1.588 MB | 4 keys, all Celery/kombu bindings; no durable state |
| Volume `sceneops_acquisition-recordings` | 0 B | capture scratch |
| 22 anonymous volumes | 1.4 kB reclaimable | unlabelled except `com.docker.volume.anonymous`, created 2026-10-30 onward |
| `data/raw` | 9.4 GB | nuScenes v1.0-mini, pinned pusht |
| `data/reference` | 3.8 GB | `nuscenes-mini-v1`: locked recordings, labels, `prepare-log.jsonl` |
| `cache/hf` | 659 MB | GroundingDINO tiny weights |

| `sceneops` database | Rows |
| --- | --- |
| `artifacts` | 12,823 (12,554 `observation_payload`) |
| `robot_runs` / `scenes` / `episodes` | 20 / 20 / 20, all `recording` |
| `source_contents` / `source_snapshots` | 0 / 0: no native content has ever been in the reference environment |
| `dataset_versions` | 5: `sceneops-ref-nuscenes-mini-full-10`, `sceneops-stream-ref-nuscenes-mini-full-10`, and the three `sceneops-test-*` journey Datasets |
| `jobs` | 211, all `succeeded` |
| `robot_states` / `missions` | 0 / 0 |

| Bucket prefix | Size | Objects / records |
| --- | --- | --- |
| `artifacts/robot_runs` | 7.6 GB | 40 / 40 (20 recordings + 20 manifests) |
| `artifacts/observation_payloads` | 6.0 GB | 12,554 / 12,554 |
| `artifacts/canonical` | 33.2 MB | 40 / 40 |
| `artifacts/labels` | 5.0 MB | 2 / 2 |
| `artifacts/runs` | 3.1 MB | 228 / 176 |
| `artifacts/datasets` | 812 KB | 7 / 7 |
| `artifacts/analytical` | 88 KB | 4 / 4 |

Every object is under a generated prefix, and objects and records reconcile except for 54 objects with no
record of their own, all under `artifacts/runs/`, and 2 `predictions_root` records naming prefixes rather
than objects. Those are 18 per-sample prediction objects below the predictions roots, and 36
`runs/evaluations/*` sample objects. Among them is `eval-sceneops-test-scene-ml-grounding-dino-recheck`, whose
name no fixed journey produces; see review item 4.

## Classification

### 1. Reproducible platform state: reset

| Resource | Reconstructed by | Note |
| --- | --- | --- |
| PostgreSQL `sceneops` | migrations + bootstrap | Job and PipelineRun history is not reproduced, and new rows have new ids and times |
| MinIO `sceneops` bucket, Recording Import artifacts | `reference-contract-bootstrap` from `data/reference` | byte-equal: the recording is the locked one, and manifests are canonical |
| MinIO `sceneops` bucket, Streaming Acquisition recordings (`run-stream-ref-*`) | replay and capture | **content-equal, not byte-equal**: a new capture has its own bytes, so its checksum and everything keyed on it changes. The equivalence verifier is what holds them to the locked content |
| MinIO derived objects (`runs/`, `labels/`, `datasets/`, `analytical/`) | the journeys | today they are old-schema outputs, which is the point of the reset |
| Kafka log | replay | bounded replay, not canonical (ADR-014) |
| Redis | workers on start | no durable state |
| `sceneops_acquisition-recordings` | capture | empty |
| `sceneops_test` / `sceneops-test` | the disposable runner | dropped by every run; `make local-reset` would remove them as well |
| `data/runs`, `data/artifacts` | the journeys | empty today |

### 2. Original source fixtures: must survive

| Resource | Guard |
| --- | --- |
| `data/raw/nuscenes` (9.4 GB) | git-ignored and not in any volume; `make local-reset` does not touch it. There is no local way to re-create it: it would be downloaded again under its license |
| `data/raw/lerobot/pusht` | `make reference-source-verify SOURCE=lerobot-pusht`, `make reference-source-fetch` re-downloads it at the pinned revision |
| `data/reference/nuscenes-mini-v1` (3.8 GB) | `make reference-data-verify`; reproducible from `data/raw/nuscenes` by `reference-data-bootstrap`, held to `corpus.lock.json` |

### 3. Configuration, credentials and development files: must survive

`.env.local`, `.env.example`, `config/reference/**` (tracked: corpus and source locks, contract, expectations,
composition), `config/channels`, `compose/**`, `migrations/**`, `cache/hf` (weights, 659 MB, download again
if lost). None is touched by `make local-reset`.

### 4. Unknown or possibly irreplaceable: manual review before any reset

| # | Resource | Why it needs a decision |
| --- | --- | --- |
| 1 | 22 anonymous volumes | `down -v` removes only volumes attached to this project's containers, so these survive a reset and nothing here says what they hold. They report 1.4 kB reclaimable. Remove by exact name if at all, and **never by `docker volume prune`** |
| 2 | `.env.airflow.local` | names an Airflow configuration; Airflow is no longer in the repository. Not touched by a reset. Yours to keep or delete |
| 3 | `sceneops-platform/*:p5-*-acceptance` images | not touched by a reset, and the only source of real old-schema outputs for a migration test once `:local` is rebuilt |
| 4 | `artifacts/runs/evaluations/eval-sceneops-test-scene-ml-grounding-dino-recheck` and other objects with no record | a manual re-check from an earlier session, by its name. If it is evidence worth keeping, copy it out first |
| 5 | `worker:local` / `api:local` (`3c76f695c1c4`, `3acb88a6eaec`) | rebuilding moves the tags and ends the ability to produce real aligned v1 output. Decide whether to keep them under another tag first (`docker tag`, no data lost) |
| 6 | `sceneops_minio-data`'s `.minio.sys` | server metadata; removed with the volume, recreated at start |

## The commands, in order (not executed)

Everything up to step 3 is read-only.

```bash
# 1. Read-only preflight
git status && git rev-parse HEAD
make disk-report
make capacity-preflight MODE=streaming_acquisition REFERENCE_SCOPE=nuscenes-mini-full-10
make capacity-preflight MODE=source_snapshot SNAPSHOTS="lerobot-pusht nuscenes-scene-0061"
make reference-data-verify REFERENCE_SCOPE=nuscenes-mini-full-10
make reference-source-verify SOURCE=lerobot-pusht
make reference-expectations
make reference-verify-mixed-expectations
docker volume ls && docker system df -v         # review the actual deletion scope again

# 2. Optional, and not destructive: keep what review item 3-5 decides to keep
docker tag sceneops-platform/worker:local sceneops-platform/worker:pre-p5
docker tag sceneops-platform/api:local sceneops-platform/api:pre-p5

# 3. Images from the current tree. Moves the :local tags; the running reference containers
#    keep the old image until they are recreated
make compose-build acquisition-image lerobot-image
docker compose --env-file .env.local --profile streaming build streaming-bridge capture

# 4. DESTRUCTIVE, after explicit authorization: tools/dev/reset_local_state.sh, which runs
#    `docker compose down -v` over the profiles worker, tools, debug, streaming, acquisition and
#    recovery, removes sceneops_{postgres,redis,minio,kafka}-data and
#    sceneops_acquisition-recordings, removes data/runs and data/artifacts, then `make local-up`.
#    It asks for confirmation unless FORCE=1.
make local-reset

# 5. Fresh acquisition: Recording Import and Streaming Acquisition from the locked corpus
make reference-contract-bootstrap
make reference-contract-verify REQUIRE_PRISTINE=1

# 6. Derived journeys of the current schemas
make e2e-scene-ml SCENE=scene-0061
make e2e-episode-learning SCENE=scene-0061

# 7. Native ingestion and mixed sources: on a disposable runtime today, see the open decision
make test-infrastructure SUITE=sources
make test-infrastructure SUITE=lerobot-episodes
make test-infrastructure SUITE=nuscenes-scenes
make test-infrastructure SUITE=mixed-sources
make test-infrastructure SUITE=nuscenes-labels

# 8. Whole verification
make reference-verify DEEP=1
make e2e-streaming-equivalence
make reference-verify-temporal \
    LEARNING_EXPORT=<manifest artifact id of the episode-learning export> \
    ALIGNED=<its aligned artifact id> \
    ANALYTICS_JOB=<job id of an export_analytics_snapshot of a contract DatasetVersion>
```

Steps 3 to 6 are what `make e2e-cleanroom` does in one command, with its own emptiness proof, two
convergence runs and a final `reference-contract-verify`. The cleanroom is the recommended form of them.
It does not cover native sources, mixed sources or the temporal verifier, so steps 7 and 8 stay.

## Open decisions for the authorization

1. **Native content in the reference environment.** Today the reference environment has no source
   content, and native and mixed workflows are only proven on disposable runtimes. The target above reads as
   though they should be part of the rebuilt environment; `publish-snapshot` plus the snapshot pipelines
   would do it, but no `make` target or contract states that state, and the golden contract's
   `REQUIRE_PRISTINE` would then refuse it unless the contract is extended.
2. **Which scope.** The contract is `nuscenes-mini-full-10` in both ingestion modes. Capacity: the VM reports
   33.6 GB free of 93.9 GB with 32.1 GB of build cache, and the bootstrap's capacity preflight refuses first
   if it does not fit.
3. **Job and PipelineRun history.** It is not reproduced. No current check depends on it, but the 211 Jobs are
   the only record of the runs that produced the current objects. Export them first if that matters.
