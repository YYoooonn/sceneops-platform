# Clean bootstrap plan (read-only inventory)

**Status: a plan, not a record of anything done.** Inspected on 2026-11-02 on
`refactor/canonical-domain-boundaries` (base commit `91bd879`, with the P5.3 and R6.1 work uncommitted) with
read-only commands only, and reviewed again after R6.1, which corrected the reconciliation and volume sections
below. Nothing here was executed: no reset, truncate, bucket deletion, prune or image rebuild of a `:local` tag. The reset needs separate authorization after its scope is reviewed again at the time,
because this inventory ages. Current commands: [test-matrix.md](../development/test-matrix.md) and
[reference-contract.md](../development/reference-contract.md).

## Target

```text
Original source fixtures (data/raw, data/reference, config/reference)
  -> fresh acquisition / native ingestion
  -> canonical Scene v5 / Episode v4
  -> unit revisions / DatasetVersions
  -> AlignedEpisode v2, one artifact per (revision, recipe), in no DatasetVersion (ADR-022)
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
| 25 anonymous volumes | 1.8 kB reclaimable | 2 attached to `sceneops-kafka-1`; 23 attached to nothing, created 2026-10-24 to 2026-11-02. See "Anonymous volumes" |
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
than objects.

**The 54 objects are not orphans.** Every one is named by a manifest that has a record, read back on
2026-11-02: they are the per-sample results those manifests pin by checksum, which by design have no record
of their own ([storage-layout.md](../architecture/storage-layout.md)).

| Run | Objects | Bytes | Registered by |
| --- | --- | --- | --- |
| `inference/infer-sceneops-test-scene-ml` + `-grounding-dino` | 18 | 342,975 | a `prediction_manifest` and a `predictions_root` each |
| `evaluations/eval-sceneops-test-scene-ml` | 9 | 147,285 | `evaluation_manifest` of `job-6dddb671a5d2` |
| `evaluations/eval-sceneops-test-scene-ml-recheck` | 9 | 147,285 | `evaluation_manifest` of `job-99bdbedb50a1` |
| `evaluations/eval-sceneops-test-scene-ml-grounding-dino` | 9 | 13,323 | `evaluation_manifest` of `job-a08c2e1b21cc` |
| `evaluations/eval-sceneops-test-scene-ml-grounding-dino-recheck` | 9 | 13,323 | `evaluation_manifest` of `job-4d6e3e6ecfef` |

The `-recheck` runs are not a manual leftover: `tools/e2e/e2e_scene_ml.sh` produces them
(`RECHECK_EVALUATION_RUN_ID`).

The reference environment holds one aligned artifact, written before ADR-018, at
`artifacts/datasets/sceneops-test-episode-learning/versions/baseline-scene-0061/episodes/episode-b9ae…/aligned/`,
with its two reports. It is of an unreadable schema and goes with the reset. Rebuilt outputs land under
`artifacts/aligned_episodes/`.

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

A note on one of those rows: the 10 Streaming Acquisition recordings are 4.07 GB of bytes **nothing can reproduce
byte for byte**. They are generated state and nothing in the contract depends on their bytes, so the reset
may remove them, but this is a loss of those exact captures rather than a rebuild of them. Copy them out first
if they are wanted as evidence.

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
| 1 | 23 unattached anonymous volumes | see "Anonymous volumes". The ones read hold an empty Redis dump or nothing, and they survive a reset. Remove by exact name if at all, and **never by `docker volume prune`** |
| 2 | `.env.airflow.local` | git-ignored, holds Airflow database and admin credentials; no compose file or `makefiles/*` names it, and Airflow is no longer in the repository. Not touched by a reset, so nothing forces this decision. Yours to keep or delete |
| 3 | `sceneops-platform/*:p5-*-acceptance` images | not touched by a reset, and the only source of real old-schema outputs for a migration test once `:local` is rebuilt |
| 4 | `eval-sceneops-test-scene-ml-grounding-dino*`, `infer-sceneops-test-scene-ml-grounding-dino` | the only real GroundingDINO outputs held. Reproduced by `make acceptance-grounding-dino` (not in the order below, needs the inference image), and the bytes are model outputs on this host: copy them out first if they are evidence |
| 5 | `worker:local` / `api:local` (`3c76f695c1c4`, `3acb88a6eaec`) | rebuilding moves the tags and ends the ability to produce real aligned v1 output. Decide whether to keep them under another tag first (`docker tag`, no data lost) |
| 6 | `sceneops_minio-data`'s `.minio.sys` | server metadata; removed with the volume, recreated at start |

### Anonymous volumes

Read on 2026-11-02 with `docker volume inspect` and one read-only `docker run --rm -v <name>:/v:ro`.

| Volumes | Created | Attached | Holds | A reset |
| --- | --- | --- | --- | --- |
| `a876fb10…` (`/etc/kafka/secrets`), `4b36ac15…` (`/mnt/shared/config`) | 2026-10-30 | `sceneops-kafka-1` | 0 B | removes them with the container, by `down -v` |
| `cbdf1f19…`, `43b40489…` | 2026-11-02 10:40, 10:41 UTC, during this inventory's `make test-infrastructure SUITE=pipelines` | nothing | 89 B, an empty Redis `dump.rdb`, both read | leaves them |
| `9348084c…`, `2c2200f7…`: new since the P5.3 inventory | 2026-11-02 09:32, 09:33 UTC, 54 s apart, the same pattern as above, during that session's suites | nothing | 89 B, `9348084c…` read as the same dump | leaves them |
| 16 more of 89 B, 3 of 0 B | 2026-10-24 to 2026-10-30 | nothing | one 89 B volume read as the same dump, one 0 B volume empty; the others are not opened, only sized | leaves them |

The two created at 10:40 and 10:41 have a measured source: they appeared during the `pipelines` suite, whose
lease-recovery tests start their own throwaway Redis (`tests/infrastructure/recovery_support.py`, `redis:7-alpine`, which
declares `VOLUME /data`). Each run of that suite leaves two such volumes, which is a small leak of the harness, not
fixed here. The older ones match that shape, which is an inference and not a recorded owner, so their ownership
stays unproven. None holds more than 89 B.

### What the reset deletes, exactly

| Resource | Removed by | Note |
| --- | --- | --- |
| Containers of the profiles worker, tools, debug, streaming, acquisition, recovery, and orphans | `down -v --remove-orphans` | |
| Volumes `sceneops_postgres-data`, `sceneops_redis-data`, `sceneops_minio-data`, `sceneops_acquisition-recordings` | `down -v` | PostgreSQL databases `sceneops`, `sceneops_test`, `postgres`; every bucket |
| Volume `sceneops_kafka-data` | `down -v`, **to be confirmed** | declared in the compose configuration, but the existing volume has no compose labels, unlike the others (created 2026-10-30). Whether `down -v` removes it was not tested. `e2e-cleanroom` checks it is gone (`volume_absent kafka-data`). If it survives, remove that exact name: a stale Kafka log must not reach the new broker |
| Anonymous volumes of those containers | `down -v` | `a876fb10…`, `4b36ac15…` |
| `data/runs`, `data/artifacts` | `rm -rf` | both 0 B; `data/runs` holds only an empty `e2e-lerobot` |

Read from `tools/dev/reset_local_state.sh`, nothing else is deleted: `data/raw`, `data/reference`, `data/inputs`,
`config/`, `.env.local`, `.env.airflow.local`, `cache/hf`, images, build cache and the unattached volumes are not
named, and `mkdir -p data/raw data/runs data/artifacts data/inputs cache/hf` only creates what is missing. None of
the original MCAP or native bytes lives in a volume: `data/raw` holds no MCAP, and the 10 locked recordings are
under `data/reference`.

## Streaming reproducibility and acceptance criteria

A repeated capture holds equal content in other bytes, measured on scene-0061 as 355,793,127 B for the locked
recording and 355,663,167 B for its capture, with other checksums. The recording's checksum is in every manifest
built from it (`lineage.source.recording_checksum`), so it moves everything keyed on it:

| Output of a new capture | Effect |
| --- | --- |
| MCAP checksum, record size | new |
| RobotRun id, recording artifact id, source identity | unchanged: `run-stream-ref-<scope>-<fixture>`, `art-robotrun-<run>` |
| Scene and Episode unit ids | unchanged: of source id and unit key, no checksum |
| Manifest checksum, manifest artifact id, revision pin, DatasetVersion content | new |
| Aligned artifact and learning export of a streaming Episode | new, because its source revision is |
| `config/reference/**` expectations | unaffected: they pin the lock's recording sha256 and rules derived from raw sources, and no capture checksum |
| `reference_contract.py fingerprint` | **not comparable across a reset**: it holds registration times and manifest checksums, and is defined for before and after within one environment |

Nothing here is a reason to regenerate an expectation, and none should be. Criteria for the rebuilt outputs:

1. **Recording Import, byte-exact.** Every recording's sha256 equals `corpus.lock.json`. Every Scene and Episode
   manifest checksum equals the value before the reset, provided no canonical build rule changed since those
   manifests were written: take `fingerprint --golden` before the reset and compare `run-ref-*` revisions by
   checksum, ignoring times. A difference means a canonical change to explain, not a baseline to update.
2. **Streaming Acquisition, content-exact.** `reference-contract-verify` and `make e2e-streaming-equivalence` pass:
   message count, per-channel counts and decoded content equal the lock's. Checksums are recorded, and not
   compared with anything earlier.
3. **Within one environment, convergent.** A second bootstrap and second journeys leave `fingerprint --golden`, artifact
   rows and MinIO objects unchanged (what `e2e-cleanroom` checks).
4. **Derived layout.** Every `aligned_episode_*` record has no `dataset_id` and a URI under `artifacts/aligned_episodes/`,
   and no object exists under `artifacts/datasets/*/versions/*/episodes/`.
5. **Temporal.** `make reference-verify-temporal` passes on a real export of each schema.
6. **Native and mixed.** The suites of step 7 pass on their disposable runtimes.

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
python3 tools/reference/reference_contract.py fingerprint --golden > <outside the repo>/golden-before-reset.json
                                                 # the baseline of acceptance criterion 1; compare by checksum

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
docker volume inspect sceneops_kafka-data        # must fail: see "What the reset deletes"

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

1. **Native content in the reference environment. Recommended: keep it out.** Keep the pristine Recording reference
   baseline and prove native and mixed sources through the disposable suites of step 7, which already compose
   every source kind in one DatasetVersion on real infrastructure. Putting them in the persistent environment
   would need a new `make` target and a contract extension, since `REQUIRE_PRISTINE` would refuse them, and no
   current check or consumer needs that state. Reconsider when one does.
2. **Which scope.** The contract is `nuscenes-mini-full-10` in both ingestion modes. Capacity: the VM reports
   33.6 GB free of 93.9 GB with 32.1 GB of build cache, and the bootstrap's capacity preflight refuses first
   if it does not fit.
3. **Job and PipelineRun history.** It is not reproduced. No current check depends on it, but the 211 Jobs, all
   `succeeded`, created 2026-10-30 06:32 to 16:43 UTC, are the only record of the runs that produced the current
   objects, and the aligned record of one of them names a Job this reset removes. Export them first if that
   matters.
4. **The Streaming Acquisition captures and the GroundingDINO outputs.** Above: generated, but not reproducible as
   the same bytes.
