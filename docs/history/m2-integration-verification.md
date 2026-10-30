# M2 integration verification and closeout (Step 7B-4)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: the journeys and test layers in
> [test-matrix.md](../development/test-matrix.md), the verifier in
> [reference-verification.md](../development/reference-verification.md), current
> constraints in [limitations.md](../architecture/limitations.md); decisions:
> [ADR-013](../adr/013-capture-records-no-kafka-progress.md),
> [ADR-015](../adr/015-multi-source-platform.md); acceptance criteria:
> [multi-source-implementation-plan.md](./multi-source-implementation-plan.md) §3.
> Preceded by [recording-platform-verification.md](./recording-platform-verification.md),
> [streaming-platform-verification.md](./streaming-platform-verification.md) and
> [streaming-recovery-verification.md](./streaming-recovery-verification.md).

```text
date         2026-10-21
branch       refactor/multi-source-platform
HEAD         82da184 (working tree: this change, uncommitted)
host         macOS (Darwin 24.3.0), repository venv; Docker Desktop VM filesystem 58.4 GB,
             15.05 GiB free at the start
images       api 04bde89be407, worker 2f886ecbceb9, publisher d1b359424cdd,
             dataset-acquisition 045d965aeea9 (current: since 9f0c8f6 the only production
             source change is reconcile.py, and the worker was rebuilt after it);
             lerobot-integration e1366c84415f built here (§4.2)
inputs       data/reference/nuscenes-mini-v1 (locked MCAPs, reference labels),
             corpus.lock.json, reference_expectations.json (unchanged)
scope        e2e-scene-ml and e2e-episode-learning on scene-0061 (each twice), alone and
             together; make test, test-integration, test-infrastructure SUITE=pipelines,
             check-commands; capacity preflight of every remaining M2 workload
not run      10-fixture Streaming Acquisition, the joint two-mode contract verification,
             e2e-streaming-equivalence, e2e-cleanroom (all refused by the preflight or
             destructive, §4)
```

## 1. Isolation

The reference environment is the reset state (26 tables, only `alembic_version`; empty
bucket), so it holds no golden contract for the `REFERENCE_DERIVED` journeys to consume, and
it was not bootstrapped. Each journey run therefore ran on its own disposable execution
runtime (`disposable_env.py run --runtime`, database `sceneops_test_ref7b4<x>`, bucket
`sceneops-test-ref7b4<x>`): the journey's own `canonical_bootstrap.sh` registered the
contract's scene-0061 RobotRun there through the production Recording Import path, and the
journey then ran unchanged against the runtime's API (`API_BASE_URL`,
`SCENEOPS_WORKER_ARTIFACT__ROOT_URI` from the runner).

The journeys' one-shot containers that the runner does not reconfigure (`worker-cli` for
the export verification, `lerobot-integration`) still load `.env.local`. They read only by
absolute `s3://sceneops-test-ref7b4<x>/…` URI, which the S3 store resolves to that bucket,
and write only under the host's `./data/runs` and `./data/inputs`, which the journeys
remove on exit. A read-only fingerprint of the reference environment (per table: row count
and md5 of the ordered rows; the bucket's keys and sizes; the bucket list; the `sceneops*`
volumes) was identical before the first run and after the last. No `sceneops-test`
container, database or bucket remained after any run.

The driver, the fingerprint, the disk watchdog (Docker VM free space every 5 s) and an
abort guard (kills the driver below 1.5 GiB free; it never acted) were scratch scripts, not
repository code. The state snapshot between passes follows `e2e_cleanroom.sh` steps 9–12:
every API collection's size, the bucket's object count and `reference_contract.py
fingerprint`.

## 2. Journeys

| Run | Journey | Pass 1 | Pass 2 | Second pass added |
| --- | --- | --- | --- | --- |
| a | `e2e-scene-ml` (mock backend) | passed, 73.7 s (incl. bootstrap) | passed, 16.2 s | 0 artifacts, 0 objects, 0 Datasets / Scenes / Episodes; 4 Jobs |
| b | `e2e-episode-learning` | passed, 85.5 s (incl. bootstrap) | passed, 39.9 s | 0 artifacts, 0 objects, 0 Datasets / Scenes / Episodes; 4 Jobs, 1 PipelineRun |
| c | both in one runtime, each twice | passed, 62.0 + 52.2 s | passed, 16.3 + 43.1 s | 0 artifacts, 0 objects, 0 Datasets / Scenes / Episodes; 8 Jobs, 1 PipelineRun |
| d | both in one runtime, then the shared-revision check (§2.1) | passed, 62.5 + 49.6 s | passed, 16.4 + 43.9 s | the same as c |

The platform fingerprint (every RobotRun, Dataset, Scene and Episode record) was identical
after the first and the second pass in every run, and no journey check failed. The Jobs and the
PipelineRun added by a second pass are execution history: the journeys' atomic re-runs
(re-import, prediction rerun, validate / profile) and the forced learning-data retry,
appended by design ([test-matrix.md](../development/test-matrix.md#execution-history)).

What the journeys showed on the new identities, scene-0061 only:

- **Scene ML.** One Scene (`sceneops-test-scene-ml/baseline-scene-0061`, composed by the
  bootstrap from the register pins); the LabelSet `labels-sceneops-test-scene-ml-scene-0061`
  imported with its coverage and converging on re-import; views, ScenarioSet, prediction
  and two evaluations pinned to what they consumed (precision 0.993399 over 1,045
  ground-truth labels, identical on the recheck evaluation); the lidar payload is one
  message of the locked recording and decodes to its 34,688 points; Scene revisions
  unchanged by every derived step.
- **Episode Learning.** One Episode aligned at 5 Hz into 98 steps; export verified (manifest,
  shards, `SceneOpsDataset`); the LeRobot round trip read 1 episode and 98 frames back with
  the official reader, each equal to the export's dense window (observation dim 4, action
  dim 3); a re-run against the populated LeRobot target refused; Episode revisions
  unchanged.

### 2.1 One revision in two DatasetVersions

In run d both journeys composed their own DatasetVersion (`sceneops-test-scene-ml` and
`sceneops-test-episode-learning`, each `baseline-scene-0061`) from the register pins of the
same RobotRun. Read back through the API afterwards:

| | Scene | Episode |
| --- | --- | --- |
| unit | `scene-82f1766f5f094306ef74c54737512da3` | `episode-b9ae0e7326cf4c263c4f2bb984d0f1e1` |
| member of both versions, same pin | yes: `scene-manifest-7cd44a4e…`, `sha256:d000b50d…` | yes: `episode-manifest-2612c607…`, `sha256:5d7bb2b6…` |
| manifest objects under `{scenes,episodes}/<unit_id>/` in the bucket | 1 | 1 |

Both versions carry the same `composition_checksum` (`sha256:af0efe43…`): it is computed from
the member pins, so two Datasets composed of the same revisions share it.

## 3. Test runs

| Command | Result |
| --- | --- |
| `make test` (with `check-boundaries`) | passed: 2,383 passed, 101 skipped over 21 suites; boundaries hold (4 apps, 13 packages, 4 integrations) |
| `make check-commands` | passed: 114 targets, 60 advertised, 8 validation commands, 4 suites, 4 journeys |
| `make test-integration` | passed: 196 + 89 + 6 = 291, none skipped |
| `make test-infrastructure SUITE=pipelines` | passed: 25 (341.7 s) |
| `make test-infrastructure SUITE=recovery` | not re-run here; 24 passed in 7B-3 at the same source (only `reconcile.py` differs from 53a9337, and the run was after that fix) |

No platform or harness defect was found: no journey check, test or verification failed
except the run refused by the capacity preflight (§4.1), which is the gate working.

## 4. Resources

### 4.1 Capacity preflight

| Workload | Required free | Docker VM free | Result |
| --- | --- | --- | --- |
| Streaming Acquisition, 10 fixtures | 18.7 GiB (growth 12.6 GiB: MinIO 7.5 GB, Kafka 5.4 GB, capture 0.56 GB) | 15.0 GiB (start) | **refused** |
| Recording Import, 10 fixtures | 11.8 GiB | 15.0 GiB (start) | fits |
| Recording Import, scene-0061 | 3.8 GiB | 15.0 / 4.3 GiB | fits |
| Streaming Acquisition, scene-0061 | 4.7 GiB | 4.3 GiB (after §4.2) | refused (computed, not run) |
| both modes, 10 fixtures (contract bootstrap, cleanroom) | about 30 GiB | 15.0 GiB (start) | refused |

One bootstrap inside run c was refused at 3.7 GiB free (3.8 required) because MinIO had not
yet released the dropped bucket of the `pipelines` suite; it passed once the free space
returned to 4.4 GiB, as observed in 7B-1 and 7B-2.

### 4.2 The LeRobot image

`lerobot-integration:local` had been removed in 7B-1 and `e2e-episode-learning` needs it.
`make lerobot-image` reused no layer: the unpinned `python:3.11-slim` base resolved to a
new digest, so every step, the dependency install included, ran again. The build took
about 3 min, brought the Docker VM from 14.43 GiB to a minimum of **0.46 GiB** free while
exporting, and left it at 4.34 GiB (image 6.99 GB on disk, build cache from 33.5 to
39.6 GB). PostgreSQL logged no error and the reference fingerprint was unchanged; nothing
else was running. Any build should be preceded by a free-space check: the capacity
preflight covers bootstraps only.

### 4.3 Measured growth

| Run | Docker VM free: start / minimum / end |
| --- | --- |
| a (Scene ML ×2) | 15.05 / 14.09 / 14.43 GiB |
| b (Episode Learning ×2) | 4.33 / 3.38 / 3.72 GiB |
| `test-integration` | 3.72 / 3.63 / 3.64 GiB |

At the end of this step the VM had about 4.4 GiB free; Docker reported build cache
39.6 GB (19.6 GB reclaimable) and images 15.9 GB (13.5 GB reclaimable). Nothing was
removed.

## 5. Readiness of the remaining M2 checks

| Check | Blocked by | What it needs |
| --- | --- | --- |
| 10-fixture Streaming Acquisition (`disposable_env.py run --streaming`, `REFERENCE_SCOPE=nuscenes-mini-full-10`, `verify --mode streaming_acquisition --deep`) | disk: 18.7 GiB free required, 4.4 GiB available | about 14.3 GiB freed by the user (build cache or the LeRobot image), or a larger VM disk; images are current |
| joint Recording / Streaming verification (`reference-contract-bootstrap` + `reference-contract-verify`, `e2e-streaming-equivalence`) | disk: about 30 GiB; it builds the contract into the reference environment | about 26 GiB freed; the user's decision to populate the reference environment |
| `e2e-cleanroom` | destructive (`make local-reset`) and disk (about 30 GiB plus image rebuilds from the tree) | explicit approval; the space above |

An interrupted 10-fixture streaming run resumes by re-running the bootstrap, except for a
run whose `RUN_END` never reached Kafka (§6).

## 6. Deferred: a run whose `RUN_END` never reached Kafka

Observed in 7B-3 (S2) and unchanged here. The run's records cannot complete it: a resumed
Capture aborts on its idle guard and finalizes nothing; reconciliation and
`acquisition-status` classify it `capture_unfinished` with health `pending` and no operator
action, the same as a live capture, so only its age shows that it was abandoned; the
streaming bootstrap's resume fails and names the manual step. Recovery is by hand: remove
the run's `.partial` directory, stream the run again under the same `robot_run_id`. The
aborted session stays in the topic until retention, so a later scan of that run fails
loudly at the second `RUN_START`.

No automatic policy was added. Discarding a partial capture or re-streaming are decisions
that cannot be taken from Kafka and the capture volume alone: a missing `RUN_END` looks the
same as a bridge that is still running or a broker that is late, and re-streaming needs the
source recording, which the platform does not own. Candidate follow-ups, each a separate
decision: an age threshold that raises `capture_unfinished` to operator-required in
`acquisition-status`; persisting the acknowledged `RUN_START` position of a re-stream so a
later Capture is pinned to it. The constraint is in
[limitations.md](../architecture/limitations.md) §2.

## 7. M2 acceptance criteria

From [multi-source-implementation-plan.md](./multi-source-implementation-plan.md) §3:

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | `make test` (with `check-boundaries`, `check-commands`), `make test-integration` and the three journeys pass | **partial** | unit, boundaries, commands, integration and `SUITE=pipelines` passed (§3); `e2e-scene-ml` and `e2e-episode-learning` passed on disposable runtimes, not on the reference environment; `e2e-streaming-equivalence` not run (needs both modes of a fixture, §4.1) |
| 2 | `e2e-cleanroom` rebuilds 20 RobotRuns / Scenes / Episodes, a second bootstrap writes nothing | **not verified** | blocked (§5) |
| 3 | the regenerated contract passes the §2 protocol for both modes | **partial** | Recording Import: 10 fixtures, deep, 0 violations, 25 / 25 controls (7B-1); Streaming Acquisition: scene-0061 only (7B-2, 7B-3); the regenerated golden expectations were not committed as a full 20-RobotRun contract run |
| 4 | an unchanged rebuild adds nothing; a changed channel selection adds one revision per unit and leaves earlier versions unchanged | verified | `SUITE=pipelines` (`test_rebuilding_an_unchanged_scope_converges`, `test_a_changed_configuration_adds_revisions_and_leaves_composed_versions_unchanged`); second bootstrap / journey passes here |
| 5 | one Scene revision is a member of two DatasetVersions with one manifest object | verified (scene-0061, Scene and Episode) | §2.1 |
| 6 | no code path creates an empty DatasetVersion or writes one after commit; a test asserts it; no status other than "exists" | verified | `packages/sceneops-db/tests/test_dataset_version_repository.py` (removed methods, no empty or inconsistent version, triggers) in `test-integration` |
| 7 | no `dataset_id` / `dataset_version` input in unit identity or canonical manifest keys | verified for identity | `test_identity_takes_no_dataset_version_snapshot_or_execution_input` (signature guard over `canonical_unit_id`, `scene_id_for`, `episode_id_for`, `manifest_artifact_id_for`); no separate grep guard over manifest keys was found |

## 8. M3 dependencies and risks

- **M3 does not depend on the unverified checks' subject.** The open items verify the
  RobotRun acquisition paths at full corpus scale and the reconstruction from nothing; M3
  adds snapshot sources and their builders on the identity, revision and composition
  contracts, which unit, integration, `pipelines`, the 10-fixture Recording Import
  verification and the journeys covered here.
- **The contract is regenerated again by M3.** M3 extends the golden contract (snapshots and
  their units), so the 10-fixture streaming run, the joint verification and the cleanroom
  run once more at M5 regardless. Running them before M3 changes the contract closes M2 as
  planned; deferring them leaves M2 acceptance criteria 2 and 3 to M5.
- **Decisions gate M3, not resources.** The plan requires OQ-1, OQ-2, OQ-3, OQ-5, OQ-6,
  OQ-9 and OQ-10 of ADR-015 §16 to be decided before M3 starts.
- **Disk is the binding constraint.** Every remaining check needs space the user must free;
  an image build can consume more than a bootstrap and is not preflighted.
- **The derived journeys have run only on disposable runtimes.** Their `REFERENCE_DERIVED`
  form (on a bootstrapped reference environment, beside the contract) runs inside
  `e2e-cleanroom`.
- **The missing-`RUN_END` case** (§6) is manual and stays so until a policy is decided.
