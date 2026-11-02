# Temporal lineage verification (P5.3)

Point-in-time evidence: what was run on 2026-11-02 and what it showed. The contract is
[ADR-021](../adr/021-cross-artifact-temporal-integrity.md); the rules and commands are in
[reference-verification.md](../development/reference-verification.md).

## Setup

| | |
| --- | --- |
| Branch / base commit | `refactor/canonical-domain-boundaries`, `91bd879` (P5.2B was already committed there), P5.3 uncommitted |
| Images under test | `sceneops-platform/worker:p5-3-acceptance` (`87d75f524d8c`), `api:p5-3-acceptance` (`c0fd57318469`), built from the final package code |
| Runtime | disposable (`make test-infrastructure`, compose project `sceneops-test`), real PostgreSQL 16 and MinIO |
| Reference stack | left on `worker:local` / `api:local` (`3c76f695c1c4` / `3acb88a6eaec`), pre-P5.1, not rebuilt or upgraded |
| Library versions | polars 1.43.2, pyarrow 25.0.1, duckdb 1.5.5 |

## The verifier against real artifacts

`make test-infrastructure SUITE=mixed-sources`: **17 passed** in 388 s. The suite builds, on the runtime's
Celery workers, one DatasetVersion of the scene-0061 RobotRun's Scene and Episode, the nuScenes scene-0061
Scene and two pusht Episodes built without transitions; aligns the Episodes, exports two of them, adds the
third by incremental export, and takes a Scene analytics snapshot. `temporal_lineage_verify.py` then runs
as a separate read-only process.

| Measure | Value |
| --- | --- |
| Checks / violations | 7,396 / 0 |
| Negative controls over the real state | 60: none undetected, with `observation_window_substituted` not applicable to this state (below) |
| Aligned artifacts verified / alignments recomputed | 3 / 3 |
| Learning exports / Episode inputs / steps compared | 2 / 5 / 520 |
| Shards verified / of which reused from the base | 6 / 2 |
| Scene rows: `scenes` / `observations` / `keyframes` | 2 / 3,569 / 39 |
| Manifest resolutions / instance comparisons | **5 / 3,623** |
| Wall time, verification plus all controls | 18.0 s (4.9 s for the one-Scene case below) |
| Rows and objects before vs after verification | identical, asserted |
| A second run on the same inputs | the same instances and checks, asserted |

The resolution count is the point of the cache: 3,623 comparisons read 5 manifests.

The suite also holds every part to its own raw-source verifier as before. Those are 8,608 mixed-source
checks with 14 controls, so the chain raw source -> manifest -> derived is proven end to end for these
units.

### Cross-family comparison

| Case | Result |
| --- | --- |
| Reference build configurations | **The Scene declares only `sensor.header_stamp`, the Episode only `vehicle.source_time`. They share no declared clock**, so they do not compare, and no equality is claimed |
| Episode built on the Scene's clock id, deliberately, for this test | `scenes` window, `AlignedEpisode` and `learning_episodes` all restore `(sensor.header_stamp, source, [recording, run-ref-nuscenes-mini-full-10-scene-0061])`, each verified against its own manifest |
| AlignedEpisode vs learning row of one Episode | equal, and equal to the manifest's |
| Two pusht Episodes with identical frame times | same clock id, origin `unit`, not comparable |
| nuScenes Scene vs the RobotRun Scene of scene-0061 | `unix_epoch` with an empty scope vs `source`, not comparable |

## CURATE_EPISODES stale reuse

Measured on real PostgreSQL before the change, on the real `create_job` path: with a v1 export pinned, the
second create returned the first, succeeded Job (`job-ac991dfeeef9`) with key
`curate_episodes:f16b3ba1…`. The new regression test failed at that point because the key had not moved.

After the change, on the same path: the old Job is not returned and the new one is `PENDING`; running it
raises `UnsupportedLearningDataSchemaError` and no object changes. Over a current export, a forced repeat
reaches the same curation id, manifest artifact and object set, and on the deployed images an unforced
repeat returns one Job.

## Mutation runs

Run on copies; the repository's files were not touched. A survivor means a test gap, not a fault.

| Target | First run | After closing the gaps |
| --- | --- | --- |
| `tools/checks/source_dispatch.py`, scope rule | 25 of 26 killed | 26 of 26 |
| `tools/reference/temporal_lineage.py` | 10 of 13 applied mutants killed | 13 of 13 |

The survivors were real test gaps, and each got a test: a Polars read of any column flagged (no allowed
control for another column), a learning row compared with its AlignedEpisode but not with the manifest
(no test had both wrong in agreement), and the manifest cache disabled (no test verified the same
revision twice in a run).

## What the runs corrected

The first real runs failed in ways no synthetic fixture had shown, and all of the failures were findings:

1. **A second DatasetVersion cannot align a revision with a recipe an earlier one used.** The record id
   derives from (Episode, checksum), and the URI names the version, so the Job fails with
   `ArtifactRecordConflictError`. `limitations.md` said the opposite and was corrected. Not fixed here: it
   would change aligned artifact identity, which P5.3 preserves. A test pins the current behaviour.
   (Follow-up: decided by [ADR-022](../adr/022-aligned-artifact-identity.md), with its own
   [record](./aligned-artifact-identity-acceptance.md).)
2. **Real Scenes and Episodes of one RobotRun share no clock**, which falsified an assumption in the first
   version of the acceptance test.
3. **Two controls had nothing to forge on real data.** Every observation is on its Scene's window clock, and
   a recording Scene has no keyframes. A control that changes nothing would otherwise fail or crash a run,
   so such a control now reports `not_applicable`, which is never counted as detected.

## Other validation

| Command | Result |
| --- | --- |
| `make test` | passed, exit 0: `apps/worker` 500 + 142 skipped (integration, run below), `apps/api` 69 + 6 skipped, `sceneops-core` 1,189, `sceneops-analytics` 300 + 3 skipped, `tools/checks` 204, `tools/reference` 260, every other suite passed |
| `make test-integration` | 253 + 150 + 6 passed, in a disposable database and bucket |
| `make test-infrastructure SUITE=pipelines`, the same images | 25 passed in 309 s |
| `make check-boundaries`, `make check-commands`, `make lint` | passed |
| `make reference-verify-temporal-selftest` | 363 checks, 51 of 51 controls detected |
| `make reference-expectations`, `make reference-verify-mixed-expectations` | no drift, nothing written |
| `make reference-verify` (the reference stack, read-only) | 127,892 checks, 0 violations, 30 of 30 controls |
| `make reference-verify-temporal` on the reference stack's old artifacts | **refused as intended**: `aligned_schema` and `export_schema` name `'v1'` |
| Reference fingerprint (rows and object count, size), before and after | unchanged |

## Not run

- `e2e-scene-ml`, `e2e-episode-learning`, `e2e-streaming-equivalence`, `e2e-cleanroom`, and any forced
  PipelineRun on the reference stack: they write to it or need it upgraded.
- `SUITE=kafka`, `SUITE=recovery` and `SUITE=boundaries`: nothing in this change touches those paths.
- A verifier run at scale. The largest table read here has 3,569 rows; this is a measurement scope, not a
  limit.
- The temporal verifier over `robot_telemetry` or `missions`, which state no instance
  ([ADR-021](../adr/021-cross-artifact-temporal-integrity.md)).
