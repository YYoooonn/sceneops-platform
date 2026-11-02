# AlignedEpisode v2 deployment acceptance (P5.1)

> Point-in-time record (2026-11-02). AlignedEpisode schema v2 and its versioned identities
> on rebuilt API and Celery workers, and the v1 → v2 boundary crossed by a real deployment
> swap on a disposable runtime. Current contracts:
> [ADR-018](../adr/018-aligned-episode-temporal-context.md),
> [derived-layer.md](../architecture/derived-layer.md),
> [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md),
> [limitations.md](../architecture/limitations.md).

## A. Environment

| Item | Value |
| --- | --- |
| Branch / HEAD | `refactor/canonical-domain-boundaries` @ `0b1240f`; the P5.1 changes are uncommitted |
| Docker VM free | 32.4 GB before the builds, 34.0 GB at the end (94 GB filesystem) |
| Celery | queues `sceneops.jobs` and `sceneops.pipeline_runs`, one worker each, on the runtime's own Redis |
| Compose project | `sceneops-test` (`compose/test-runtime.yaml`), database `sceneops_test`, bucket `sceneops-test`, on the shared PostgreSQL and MinIO servers |

| Image | ID | Code |
| --- | --- | --- |
| `worker:local` | `3c76f695c1c4` (2026-10-30) | pre-P5.1, artifact schema `v1`; has no `sceneops_core.temporal`, so it predates `0b1240f` as well. Runs the reference stack |
| `api:local` | `3acb88a6eaec` (2026-10-30) | same |
| `worker:p5-1-acceptance` | `6492396581db` | this working tree, schema `v2` |
| `api:p5-1-acceptance` | `a8ea86416870` | this working tree, schema `v2` |

Code verification: every `.py` under `packages/` and the app (worker 816 files, API 724)
was hashed inside each new image and compared with the working tree: 0 differences after
the build and 0 at the end. The running containers were checked separately for these
modules, in both acceptance runs:

```text
102027cc…  sceneops_core/episodes/alignment/persistence.py
895b4fd2…  sceneops_core/executions/key.py
```

The API image was rebuilt because the API computes execution keys
(`compute_execution_key`): a stale API would hash a request without the schema version and
return the v1 Job whatever the workers run. Only matched API and worker pairs were run.

The runtime's images were hard-coded to the tags the reference stack runs. The two new
tags were selected through `TEST_WORKER_IMAGE` and `TEST_API_IMAGE`, added for this work,
so `worker:local` and `api:local` did not move.

## B. Suite on the new images

`TEST_WORKER_IMAGE=sceneops-platform/worker:p5-1-acceptance TEST_API_IMAGE=sceneops-platform/api:p5-1-acceptance make test-infrastructure SUITE=lerobot-episodes`

**11 passed in 86.8 s.** Capacity preflight `ok`. Raw-source verifier: 7,631 checks on the
whole corpus, 11 / 11 negative controls detected. Episode 0 built without `next.*` aligned
to 161 steps carrying 161 video-frame references. An Episode with `next.*` is refused.

## C. Migration across a deployment swap

One disposable runtime, in the order a real upgrade would see. A one-off script drove it
through `disposable_env.py run --runtime`; it is not in the repository because it needs a
pre-P5.1 image.

```text
Phase A  old API + old workers
         publish and register pusht, build Episodes 0 and 1 without next.*, compose
         direct ALIGN_EPISODE, VALIDATE, PROFILE, EXPORT; learning PipelineRuns (LeRobot, recording)
Swap     `compose up -d` with the new images: same database, bucket and Redis
Phase B  new API + new workers: the checks below
```

The final run held 82 of 82 checks. Two earlier runs of the script held all of theirs (78,
79) and grew it. A first attempt crashed on a defect of the script, before any check.

### Celery execution

| Check | Observed |
| --- | --- |
| Executed by the new worker | Job `succeeded` with `workerId celery:<task id>`, events `created → queued → locked → started → … → succeeded`, and the jobs worker's log names the Job |
| Output schema | `schemaVersion: "v2"` |
| Fields added against v1, same Episode and config | exactly `clockOrigin`, `clockScope`; no field removed or changed |
| `steps` against v1 | identical JSON |
| Video references (Episodes 0 and 1) | 161 and 118, each with its own `video_frame` selector, equal to the manifest's PayloadRef |
| Restored instance | equal to `manifest.clock_instance(source_clock)`, also through `restore_clock_instance` on the raw JSON |
| Semantics version | `v3` in both schemas |
| ArtifactRecord | pins the URI and checksum; the checksum is the sha256 of the stored bytes |
| Validate / profile of v2 | `valid: true`, 0 issues; the profile pins the same revision |

| Source | `sourceClock` | `clockOrigin` | `clockScope` |
| --- | --- | --- | --- |
| LeRobot Episode 0 | `lerobot.frame_time` | `unit` | `[content, content-0322a8b9…, 0]` |
| LeRobot Episode 1 | `lerobot.frame_time` | `unit` | `[content, content-0322a8b9…, 1]` |
| Recording, scene-0061 | `vehicle.source_time` | `source` | `[recording, run-ref-nuscenes-mini-full-10-scene-0061]` |

`require_comparable` refuses LeRobot Episode 0 against Episode 1, whose frame times are on
the same clock id. `make check-boundaries` found no source dispatch outside the listed
sites, and both source kinds ran through one engine.

### v1 and v2 side by side

| Requirement | Observed |
| --- | --- |
| v1 object stays intact | byte-identical at the end; every object from before the swap kept its size |
| v2 has a distinct identity | other URI, artifact id and checksum: alignment key `cfdad63e…` → `f653f2ec…` under the same source hash |
| Repeat reuses v2 | a forced Job writes to the same URI, checksum and artifact id and adds no object or record |
| v1 is refused | `load_aligned_episode_artifact` and the envelope model raise; forced validate, profile and export fail on the worker with the schema refusal message |
| No write-once conflict | none in Job results or the worker log |

Determinism across three fresh environments: manifest checksums, v1 and v2 URIs, checksums
and artifact ids, Job and PipelineRun execution keys, clock triples and export ids were all
identical.

### Job identity

| Request after the swap | Result |
| --- | --- |
| Identical unforced ALIGN_EPISODE | new Job; key `align_episode:ed527f82…` → `align_episode:1c14c56a…`; the v1 Job is not returned |
| Repeated | same v2 Job, not dispatched, no Job or record added |
| `force: true` | new Job, converging on the same artifact |

### PipelineRun identity

A PipelineRun's key is unchanged across the schema
(`episode_learning_data_building:b6502b50…`), and its task Jobs have no execution key
(`NULL`). Measured with recording Episodes:

| Request after the swap | Result |
| --- | --- |
| Identical unforced | **the old succeeded run**; not dispatched, no run, Job or object added; its align result names the v1 artifacts |
| `force: true` | new run, same key; its align task converges on the direct Job's v2 artifacts; other export id |
| Identical unforced again | the forced run |
| Old run and v1 export | stay as records |

Nothing was forced on the reference environment.

### Finding: an unforced read of v1 can succeed without reading

VALIDATE, PROFILE and EXPORT keep their keys across the schema, so an unforced identical
request returns a Job that succeeded before the upgrade, and no read happens:

| v1 artifact | Unforced request after the swap |
| --- | --- |
| Never validated (recording) | new Job, **fails** with the schema refusal |
| Validated before the upgrade (LeRobot) | **the old Job, `succeeded`, `valid: true`**, not dispatched |
| Profile and export that succeeded before the upgrade | the old Job, `succeeded`, not dispatched |

The chain was run end to end on LeRobot: unforced PipelineRun → old run → its v1 pin →
unforced validate → old `valid: true`. The current reader refuses that same object. The
records are true of the checksum they pin, and a request has to name the v1 artifact to get
one, but a client following the chain sees no error.

### Canonical manifests

Rebuilds on the new workers changed nothing: LeRobot created 0 manifests, and the forced
recording rebuilds left the Scene and Episode revision lists equal. The rebuilt recording
checksums `sha256:de10290e…` (Scene v5) and `sha256:25bdf7ab…` (Episode v4) also equal the
reference environment's.

### LeRobot learning export

Episodes 0 and 1, aligned without `next.*`, went through the forced learning pipeline on
the new workers: 2 episodes, 279 steps, 837 signals. This shows it runs. Its tables were not
inspected, and they keep clock ids only until the learning schema changes.

## D. Regression

| Command | Result |
| --- | --- |
| Focused alignment, persistence, identity and handler modules | 138 passed |
| `pytest packages/sceneops-core/tests -k align` | 184 passed, 962 deselected |
| `make test` | 21 suites, 3,319 passed, 145 skipped, 0 failed |
| `make check-boundaries` | pass |
| `make test-integration` | 392 passed (253 + 133 + 6), 0 skipped; includes the 5 upgrade tests on real PostgreSQL and MinIO |
| `make reference-contract-verify` | `violations: []` |
| `make reference-verify DEEP=1` | 140,446 checks, 0 violations, 30 / 30 negative controls detected |
| `make canonical-verify REFERENCE_SCOPE=nuscenes-mini-full-10` | pass |

`make reference-expectations WRITE=1` was not run.

## E. Reference environment

Fingerprinted before any test, after the regression and verification commands, and again
at the end of all runs: 0 differences.

| Scope | Compared | State |
| --- | --- | --- |
| PostgreSQL | row count and content md5 of all 28 tables, migration head, database list | 12,823 artifacts, 211 Jobs, 44 PipelineRuns, 20 + 20 revisions; head `a9d4e7b2c6f3` |
| MinIO | key, size, ETag and modification time of every object; sha256 of the 3 aligned objects | 12,875 objects, 14.49 GB; v1 artifact `aligned-388e520e…` sha256 `995829e7…` |
| Source trees | sha256 of every file | `data/raw/lerobot/pusht`, `data/raw/nuscenes` (39,058 files), `data/reference`, `data/inputs` |
| Containers | id, image, start time, restart count | 7 unchanged; no restart or recreation |
| Image tags, Redis queues | `worker:local`, `api:local`; queue lengths | unmoved; 0 |
| Expectations and verifiers | sha256 of 50 files under `config` and `tools/reference` | unchanged |

Each runtime and its database and bucket were removed by the runner.

## F. Not established

- The reference stack still runs the pre-P5.1 images, and its v1 artifact was not migrated.
- `make e2e-episode-learning` was not run: it writes to the reference environment.
- A mixed API and worker deployment, a worker crash during a v2 write, and the
  `pipelines`, `recovery`, `nuscenes-scenes`, `mixed-sources` and `nuscenes-labels` suites
  on the new images.
- The LeRobot export of a LeRobot Episode.

Unsupported by design at this point: decoding video frames, aligning `transition` streams,
and clock context in the learning and Scene analytics tables.

Separately, a succeeded ALIGN_EPISODE Job reports `resolve_source_manifest` as `succeeded`
and its other three steps as `pending`, on both the old and the new images. It does not
come from P5.1 and was left alone.
