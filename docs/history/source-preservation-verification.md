# Source preservation verification (M3 S1)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: publication and registration in
> [ingress-boundaries.md](../architecture/ingress-boundaries.md) §3.1, the records in
> [data-model.md](../architecture/data-model.md) §5.1, the object layout in
> [storage-layout.md](../architecture/storage-layout.md) §3, the suites in
> [test-matrix.md](../development/test-matrix.md); decision:
> [ADR-015](../adr/015-multi-source-platform.md) §5; acceptance criteria:
> [multi-source-implementation-plan.md](./multi-source-implementation-plan.md) §4 (S1).

```text
date         2026-10-22
branch       refactor/multi-source-platform
HEAD         5dacf00 (working tree: S1-B, uncommitted)
host         macOS 15.3.2 (Darwin 24.3.0), Apple M1 Pro, repository venv (Python 3.11)
docker       29.8.2; Docker Desktop VM filesystem 94 GB, 82 GB free during the runs
             (the VM had been emptied before this work: no image, volume or build cache)
images       postgres:16, sceneops-minio:RELEASE.2024-11-07T00-52-20Z, built here from
             the final working tree: api f8eb6ccd59cf, worker a5b01c1274a9,
             publisher 6b38f7d6b8f9 (run 1 of SUITE=sources used an earlier build that
             differed only in formatting and one unused import)
inputs       data/raw/lerobot/pusht (make reference-source-verify: 7 files, 7,684,382 B,
             verified against source.lock.json); data/raw/nuscenes (v1.0-mini, local)
declarations config/reference/source-snapshots.json
```

## 1. What was run

| Command | Result |
| --- | --- |
| `make test` | 21 suites: 2,531 passed, 109 skipped, 0 failed |
| `make test-integration` (disposable database + bucket) | 312 passed (209 db + storage, 97 worker / analytics / execution, 6 api) |
| `make test-infrastructure SUITE=sources` (disposable runtime), twice | capacity preflight `ok`; 6 passed (101.5 s, 96.2 s) |
| `make check`, `make lint`, `make check-boundaries`, `make check-commands` | pass |
| `alembic downgrade -1` / `upgrade head` on a disposable database | `b6e2d8f4a1c7` ⇄ `a3f7c1e9d5b8` |
| publisher image: `publish-snapshot` of pusht into a local root, raw tree mounted read-only | 7 files, 7,684,382 B |

The new tests in these counts: core decision table (`test_source_registry.py`, 11),
registrar unit tests with a transactional fake registry (`apps/worker/tests/sources`,
25), Publisher process tests (`apps/publisher/tests/test_cli.py`, +5), API routes (4),
snapshot capacity preflight (+5), PostgreSQL invariants and overlapping transactions
(`test_source_registry_repository.py`, 11), registration on real MinIO + PostgreSQL
(`test_source_registration_integration.py`, 8), storage key round trips (+2).

## 2. Acceptance against S1

Numbers are from `SUITE=sources`; expectations were derived without the code under
test: pusht from its raw-source lock, the slice by walking the raw tree with plain string
rules and hashing every file with `hashlib`.

| S1 criterion | Observed |
| --- | --- |
| 1 pusht publishes 7 files / 7,684,382 B, one content and one snapshot; inventory equals an independent hash | inventory equal to the lock (path, size, sha256); Job result `created` / `created`; `GET /source-snapshots/{id}` and `/source-contents/{id}` return the declaration and 7 / 7,684,382 |
| 2 the scene-0061 slice publishes 2,989 files / 519,531,462 B, incl. `+` keys, 8 CAN extracts, 4 maps, `LICENSE`, every key read back on MinIO | inventory equal to the independent walk; 2,963 keys contain `+`; the listing under the content's `files/` prefix equals the inventory paths and sizes; all 2,989 objects read back and hashed equal |
| 3 a second pusht declaration with another `upstream_revision` and identical bytes: one content, two snapshots, no file object written | publication wrote 0 files, reused 7, wrote only its descriptor (the only new object); registration `existing` / `created`; both snapshots listed for the content |
| 4 the §3 case table; a pattern matching nothing or using `[` fails publication | other bytes and other annotations under the pusht declaration: publication refused before any write (bucket unchanged); `nothing/**` and `data/[a]` refused; the same bytes as `not-lerobot` published (0 files written) and its registration Job failed with `ContentFormatConflictError`, no row written. One declaration with other bytes or another descriptor at registration (the descriptor replaced out of band) and every case under concurrency: integration and unit tests (§3) |
| 5 crash before the inventory, crash after the descriptor, a file altered in the store, concurrent registrations | integration tests on MinIO + PostgreSQL (§3) |
| 6 re-running publication and registration writes nothing | re-publication wrote no object (bucket listing identical); an identical `POST /jobs` returned the succeeded Job; a forced new registration Job reported `existing` / `existing`; snapshot listings identical |
| 7 `capacity-preflight` refuses a snapshot that does not fit | unit-tested at the boundary (`required − 1` byte refused); the real preflight reported 2,996 files, 527,215,844 B, 3,880,245,277 B required, 87.2–87.5 GB measured free |

## 3. Failure, conflict and concurrency results

On real MinIO and PostgreSQL (`make test-integration`):

- **Unusual keys.** Files named with `+`, a space, `?`, `#`, `;`, `%2B` and non-ASCII
  characters were published, listed back with exactly their keys, registered, and
  materialized byte-identical through `StoredContentFiles`.
- **Interrupted publication.** A publisher whose store failed after 3 writes left no
  descriptor; registration failed `SnapshotNotPublishedError`; the re-run reused the 3
  objects, wrote the rest and the descriptor; registration then succeeded.
- **Altered object.** One preserved object overwritten with other bytes of the same
  size failed registration (`ContentVerificationError`, sha256) with no content,
  snapshot or ArtifactRecord row.
- **Untrusted location.** A descriptor copied to another prefix of the same bucket was
  refused (`UntrustedSourceLocationError`); the unit tests also assert that nothing is
  read for seven other spellings (another root, `..`, `//`, another object name).
- **Concurrency** (two sessions released by a barrier): two registrations of one
  snapshot → one `created`, one `existing`, one row each; two snapshots of one content
  → one content row, two snapshots, one inventory record; the same bytes under two
  formats → one success, one `ContentFormatConflictError`, one snapshot.
- **PostgreSQL alone** (rows inserted around the repository): UPDATE of either table
  and DELETE of a snapshot or a declared content are refused; a content without a
  snapshot cannot commit (deferred trigger); a snapshot of another format violates
  `fk_source_snapshots_content`; overlapping transactions on one key wait (asserted
  blocked for 0.3 s) and then converge or fail as above.
- **Job path.** `REGISTER_SOURCE_SNAPSHOT` through `JobRunner`: the first Job inserted
  the records with its `job_id` on both ArtifactRecords, the second reported
  `existing` / `existing` and changed nothing.

## 4. Measurements

`SUITE=sources`, local stack above, two runs (seconds, run 1 / run 2; wall clock of
the Publisher process, or of the Job from `POST /jobs` to its terminal state with 1 s
polling, so a Job is never measured below ~1 s):

```text
                         pusht (7 files, 7.3 MiB)   scene-0061 slice (2,989 files, 495 MiB)
publish (first)          0.54 / 0.55                 52.9 / 47.2
publish (re-run)         0.44 / 0.41                 26.9 / 27.8
register (first)         1.21 / 1.20                 4.10 / 4.09
register (new Job)       1.04 / 1.04                 1.06 / 1.04
read back + hash (test)  —                           4.66 / 4.69   (16 concurrent reads)
bucket after each run    3,002 objects, 527,820,150 bytes
```

Publication is sequential per file and, per file, an existence check, a write and a
read-back; a re-run skips the write. The first registration of the slice lists once
and hashes every object with 8 concurrent reads. A registration whose snapshot is
registered reads only the descriptor.

## 5. Defect found and fixed

`S3ArtifactStore` derived the object key with `urlparse(uri).path`, which ends at `?`
or `#`. A preserved file named `query?part.txt` would have been written to and read
from the key `query`, so two such files would collide and a third object would be
addressed by the wrong name. Preserved paths are verbatim (the inventory contract is
unchanged), so the backends now take everything after the bucket (S3) or the authority
(`file://`) as the key. Regression tests: `test_unusual_keys_round_trip_verbatim`
(MinIO) and `test_file_uri_keeps_query_and_fragment_characters` (local).

## 6. Not covered

- Two runs of each measurement on one machine; no claim beyond this workload.
- Files are read whole into memory by publication and registration (largest file
  6.9 MB). No file larger than that was published.
- Objects left by an interrupted publication or a refused registration are not
  classified or removed; the suite's `not-lerobot` descriptor stays in its bucket until
  the bucket is dropped.
- The Publisher ran as a host process in the suite; its image was exercised once by
  hand against a local root, not against MinIO.
- No native reader, builder or canonical unit exists yet (S2–S4).
