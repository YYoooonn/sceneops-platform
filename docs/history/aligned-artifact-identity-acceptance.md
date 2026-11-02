# Aligned artifact identity across DatasetVersions (R6.1)

Point-in-time evidence: what was run on 2026-11-02 and what it showed. The contract is
[ADR-022](../adr/022-aligned-artifact-identity.md).

## Setup

| | |
| --- | --- |
| Branch / base commit | `refactor/canonical-domain-boundaries`, `91bd879`, with the P5.3 and R6.1 work uncommitted |
| Images under test | `sceneops-platform/worker:r6-1-acceptance` (`e1126c2e9c30`), `api:r6-1-acceptance` (`3bd1c8e9e31f`). Two docstrings changed after the build; no behaviour did |
| Runtime | disposable database and bucket (`make test-integration`), and the disposable execution runtime (`make test-infrastructure`), real PostgreSQL 16 and MinIO |
| Reference stack | left on `worker:local` / `api:local` (`3c76f695c1c4` / `3acb88a6eaec`), pre-P5.1, not rebuilt or restarted |
| Workload | synthetic LeRobot Episodes, built, registered and composed by the real path; and on the deployed suite, the scene-0061 RobotRun and pusht |

## The failure, before the fix

A temporary test ran the real `AlignEpisodeJobHandler` from two DatasetVersions of one pinned revision with one
recipe, in both orders: the second version composed before the first alignment, and after it. Both failed the same way.

| Step | Result |
| --- | --- |
| First alignment | record `aligned-88f3a6a1…`, `sha256:f90f421f…`, object under `versions/aligned-upgrade/episodes/…/aligned/8ce0dbf288a210a3/f653f2ec41811087.json` |
| Second alignment | `ArtifactRecordConflictError`: the same id, the same checksum, a URI under `versions/b/` |
| Objects after | 2: the first, and one under `versions/b/` that no record references |
| Records after | 1, unchanged |

The checksums were equal across versions in both orders, so the DatasetVersion was not reaching the bytes.

## Found while fixing it

1. **Reports had the same disagreement**: id of (aligned artifact, checksum), URI naming the version.
2. **Validate and profile used the record's scope as their only membership check**, and removing it from the record
   would have left them with none.
3. **`EXPORT_LEARNING_DATA` checked no membership.** An input from outside the version was accepted.
4. **`ALIGN_EPISODE` accepted a pin of any registered revision of the Episode**, including one the version does not pin.
5. **Sharing records makes a deadlock reachable.** Two Jobs registering shared Episodes in opposite orders crossed.
   With the sort removed, `test_versions_sharing_two_episodes_in_opposite_orders_do_not_deadlock` fails with
   `asyncpg.exceptions.DeadlockDetectedError`; with it, it passes.
6. **`CURATE_EPISODES` does not compare the export manifest's DatasetVersion with the one it names.** Not changed: it
   does not depend on aligned identity. A follow-up.

Learning exports of the same inputs from two versions do not conflict: their tables have `dataset_id` and
`dataset_version` columns, so the bytes differ, and each version has its own objects and records (measured).

## Results

| Command | Result |
| --- | --- |
| `make test` | 3,681 passed, 0 failed, 179 skipped (real-infrastructure modules, which skip there) |
| `make test-integration` | 253 + 167 + 6 passed, 0 failed |
| `apps/worker/tests/episodes/test_aligned_identity_across_versions_integration.py` | 17 passed, included above |
| `make test-infrastructure SUITE=mixed-sources` on the images above | 17 passed in 426 s, including a second version through deployed Celery workers and the temporal lineage verifier |
| `make test-infrastructure SUITE=lerobot-episodes` on the images above | 25 passed in 310 s |
| `make test-infrastructure SUITE=pipelines` on the images above | 11 passed in 84 s |
| `make reference-verify-temporal-selftest` | 363 checks, 0 violations, 51 of 51 negative controls detected |
| `make reference-contract-verify` on the reference stack, read-only | no violations |
| `make check-boundaries`, `make check-commands`, `make lint`, pre-commit on the changed `apps` and `packages` files | pass |

The reference stack was the same after as before: 12,823 records, 12,875 objects, 211 Jobs and 5 DatasetVersions.

## What was not run

- `e2e-episode-learning`, `e2e-scene-ml` and `e2e-cleanroom`: they write to the reference stack, which runs images older
  than this change. The assertion changed in `tools/e2e/e2e_episode_learning.sh` has therefore never been executed.
- `SUITE=nuscenes-scenes`, `sources`, `nuscenes-labels`, `recovery`, `kafka`, `boundaries`: they do not align.
- Anything on real concurrency beyond two versions and six parallel requests.
- A crash at any boundary other than the commit.
