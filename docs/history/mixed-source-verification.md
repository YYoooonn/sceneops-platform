# Mixed-source composition verification (M3 S5)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: composition in
> [data-model.md](../architecture/data-model.md) and
> [ADR-015](../adr/015-multi-source-platform.md) §7, payload dispatch in
> [ingress-boundaries.md](../architecture/ingress-boundaries.md) §4, the verifier's rules
> in [reference-verification.md](../development/reference-verification.md) ("Mixed-source
> DatasetVersions"), the suites in [test-matrix.md](../development/test-matrix.md);
> acceptance criteria: [multi-source-implementation-plan.md](./multi-source-implementation-plan.md)
> §4 (S5).

```text
date         2026-10-28
branch       refactor/multi-source-platform
HEAD         8b5078a (working tree: S5, uncommitted)
host         macOS 15.3.2 (Darwin 24.3.0), Apple M1 Pro, repository venv (Python 3.11)
docker       Docker Desktop VM, 55.8–57.0 GB free during the runs (capacity preflight);
             the reference stack's postgres / redis / minio served the disposable
             databases and buckets; the reference database, bucket, API and workers
             were neither read nor written
images       built here from the working tree: worker 2e1fa8e5f75c (with the analytics
             export fix below), api 6fb7e2f09f91
inputs       the locked scene-0061 recording (data/reference/nuscenes-mini-v1),
             data/raw/nuscenes (v1.0-mini) selected by the nuscenes-scene-0061
             declaration, data/raw/lerobot/pusht at 7628202a…
composition  config/reference/mixed-sources/composition.json
```

## 1. What was run

| Command | Result |
| --- | --- |
| `mixed_source_verify.py derive --write`, then `derive` (no stack) | expectations written from the raw inputs only; 7 checks, 0 violations |
| `mixed_source_verify.py selftest` (no stack) | 17,203 checks, 0 violations; 29 / 29 controls detected (14 on a composition by pins, 15 by selector) |
| `make test-infrastructure SUITE=mixed-sources` (disposable runtime) | first run 10 passed, 2 failed (two test expectations, §3); second run 11 passed, 1 failed (the analytics export defect, §3); after the fix and an image rebuild, **12 passed in 329 s**; capacity preflight `ok` (3.88 GB required, 55.8 GB VM free) |
| `make test-integration` (disposable database + bucket) | 335 passed (209 db + storage, 120 worker / analytics / execution incl. 12 mixed-source composition tests, 6 api) |
| `make test` | 21 suites: 2,740 passed, 132 skipped, 0 failed (incl. `make check-boundaries`, the source-dispatch guard and the export regression test) |
| `make test-infrastructure SUITE=pipelines` (recording regression, disposable runtime, final images) | 25 passed in 310.5 s |
| `make lint`, `make check`, `make check-commands`, `tools/checks/source_dispatch.py` | pass |

## 2. Results against the S5 acceptance criteria

| # | Criterion | Observed |
| --- | --- | --- |
| 1 | A DatasetVersion of the RobotRun `scene-0061` Scene, the nuScenes Scene and the pusht Episodes, composed by pins and by source selector, validates and reports both domains and sources | `COMPOSE_DATASET_VERSION` by 209 pins (the RobotRun's Scene and Episode, the nuScenes Scene, 206 pusht Episodes) and by a four-clause selector (`{scene, episode} × {recording, content}`) committed the same `composition_checksum`. Summaries equal the raw derivation: 2 Scenes, 3,569 observations, 39 keyframes, 14 observed channels, 207 Episodes. The members listings report `sourceKind` `recording` and `content` in both domains, and every content member has `robotRunId` null. `VALIDATE_SCENE` of both Scenes in one Job and `VALIDATE_EPISODE` of all 207 Episodes in one Job returned `ready`; profiling matched; the version's quality endpoint reports `ready`, 2 / 2 Scenes. The source mix is reported per member, not in the stored summary (§4) |
| 2 | No module downstream of canonical manifests branches on `source_kind` or a format outside the payload locator (grep guard) | `tools/checks/source_dispatch.py` (AST, run by `make test`): every comparison on `source_kind` / `format` / a source-kind, payload-kind or format literal, every `isinstance` / `match` on a binding, revision, pinned source or payload class, and every dict keyed by a format lies in a listed upstream module or is one of five listed downstream sites: the payload locator, the analytics and alignment capability refusals, the sample-view label-anchor identity check and the LeRobot export's own target format. A listed site that disappears fails the check too |
| 3 | Re-running every S1–S4 step writes no row, artifact or object | publication and `REGISTER_SOURCE_SNAPSHOT` of both snapshots (`existing` / `existing`), `BUILD_SNAPSHOT_*` (0 manifests created) and `REGISTER_*` of both contents, `BUILD_RECORDING_*` and `REGISTER_*` of the RobotRun, and both compositions (`created: false`): the row count and a digest of every row of `robot_runs`, `source_contents`, `source_snapshots`, `scenes`, `scene_revisions`, `episodes`, `episode_revisions`, `dataset_versions`, `dataset_version_members` and `artifacts`, and the bucket listing with sizes, were identical before and after (56.5 s) |
| 4 | The verifier passes on S1–S4 output, every negative control fails it, the result is recorded here, then the slice golden expectations are committed | `mixed_source_verify.py verify` passed on both versions (8,608 checks and 14 / 14 controls on the pins version; on the selector version every control including `selector_clause` detected and the selector check passed); each part also passed its own verifier with all its controls detected (`nuscenes_scene_verify.py` for scene-0061, `lerobot_verify.py` for all 206 Episodes, `reference_verify.py --mode recording_import --fixture scene-0061`). The golden expectations are `config/reference/mixed-sources/expectations.json`, derived from the raw inputs only and re-derived by every run; they are committed with this record |

Further results from the task:

| Subject | Observed |
| --- | --- |
| No synthetic RobotRun | the runtime holds exactly one RobotRun, the baseline's; every unit without a RobotRun is a content unit. Integration: publishing, registering, building, registering and composing two contents adds no `robot_runs` row |
| Identical content through another snapshot | a second declaration of each content registered as `content: existing`, `snapshot: created`; building through it named the new snapshot in its Job result, created no manifest, re-registration returned the same pins, the manifest records kept the first build's Job, and no row or object changed |
| Identity independent of membership | integration: every unit id recomputes from `(domain, source_kind, source_id, unit_key)` written out in the test; composing the same revisions into two versions changed no unit, revision, artifact or object; no stored content manifest contains a JSON token of the Dataset id or either version name. The verifier checks the same tokens on every member |
| Concurrency | integration: four identical compositions of one name (`asyncio.gather`) — one `created`, three converged, one checksum; three different compositions of another name — one committed, two `DatasetVersionConflictError`. Infrastructure: three separate Jobs composing one name on the runtime's workers — all succeeded, exactly one `created`; two different compositions of one name — one succeeded, one failed "never rebound". Whether the calls overlapped in time was not instrumented; the recording-only `_race` tests prove the overlapping path |
| Negative compositions (nothing written) | wrong pin (a Scene unit at an Episode manifest's checksum, artifact id recomputed), selector clauses naming the RobotRun as content, a content as recording, the nuScenes content's Episodes, the LeRobot content's Scenes, a foreign fingerprint, a rebinding of a used name (Job fails, all ten domain tables unchanged); two revisions of one unit and a pin whose artifact id is another revision's (refused at `POST /jobs`, 400); two revisions of one content unit by two selector clauses; a manifest whose preserved file was deleted from MinIO is refused by the registration gate, so its pin composes nothing |

Measurements (`mixed_source_measurements`, final run): nuScenes slice publish + register
54.5 s (first registration hashes 2,989 files), pusht 1.7 s; `snapshot_scene_building`
3.0 s, `snapshot_episode_building` (206) 15.1 s; composition of 209 pins 1.2 s; mixed
verifier 27.4 s (pins) / 27.7 s (selector); per-source verifiers 13.1 s (nuScenes), 16.0 s
(pusht), 10.0 s (recording); re-running every step 56.5 s.

## 3. Findings

- **Analytics export left a table behind when it refused (fixed).**
  `EXPORT_ANALYTICS_SNAPSHOT` wrote the `scenes` table, then `build_observations_table`
  refused the content Scene (`UnsupportedSceneContentError`): the Job failed with the
  explicit error and registered no ArtifactRecord, but one Parquet object
  (`…/pins/snapshots/scenes-<sha>.parquet`, 8,581 B) stayed in the bucket. Any version
  holding a content Scene reached it. The handler now builds every requested table before
  writing any; regression test
  `test_a_refused_table_fails_the_export_before_any_table_is_written`. The suite then
  observed an unchanged bucket listing after the refusal.
- **Two test expectations were wrong, not the platform.** The sample-view result reports
  a skipped Scene as `{reason: scene_lacks_anchor_channel, scene_id}`, not with the
  exception text. An identical `POST /jobs` while the first is in flight returns that Job
  ([jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md), request identity), so
  "three concurrent identical Jobs" needs three execution keys; the test gives each
  request its own `metadata`.
- **Payload bytes are not re-verified on read by every consumer (open, pre-existing).**
  The payload locator verifies a reference against its ArtifactRecord or inventory entry
  and returns a URI; it states that the bytes are the reader's to verify. The detection
  lidar lift reads the bytes and decodes them without comparing their sha256 or size
  with the reference, for either reference kind, and the camera image is read by the
  model server. ADR-015 §6.4 says payload bytes are re-verified on every pinned read.
  Not changed here: it is not specific to mixed sources and no content payload is
  decoded today (a preserved `.pcd.bin` lift is refused by media type).
- **Consumers of a mixed version.** Sample views build per Scene and skip the RobotRun
  Scene, which has no `LIDAR_TOP`, with its reason; alignment refuses a pusht member and
  aligns the RobotRun Episode of the same version; the analytics export refuses the whole
  version because one Scene references preserved files.
- **Pre-existing:** `tests/infrastructure/test_nuscenes_scene_acceptance.py` imports
  `pathlib.Path` unused (ruff F401); the file is outside `make lint` / pre-commit scope.

## 4. Limits of this verification

- **Summaries carry no source breakdown.** The stored summaries are the ADR-015 §7
  fields (`scene_count`, `observation_count`, `keyframe_count`, `observed_channels`,
  `episode_count`). Which sources a version mixes is answered by its members (each
  revision carries `source_kind` and its RobotRun or content id); no summary field was
  added.
- **Recording pins are verified, not derived.** The verifier does not recompute payload
  artifact ids, so a recording member's manifest checksum enters the expected composition
  checksum after the recording manifest checks pass. Content pins are derived whole.
- **One composition.** The spec covers one RobotRun fixture, one nuScenes scene and one
  LeRobot snapshot. The recording parts of the other contract fixtures and the full mini
  snapshot (M5) were not composed.
- **Not rerun:** the `sources`, `lerobot-episodes`, `nuscenes-scenes` suites (their code
  paths are unchanged; the mixed suite publishes, builds and verifies both contents
  again), the E2E journeys and the clean room. The analytics export change keeps the
  success path's output (same tables and records; the handler's unit tests pass) but
  `e2e-scene-ml`, which exports analytics, was not rerun.
- **Reference environment.** Its database is still not migrated past S2; it was not
  touched.

## 5. Readiness for S6

S6 (native nuScenes labels) builds on what this slice verified: the nuScenes Scene's
keyframe groups carry `native_ref = sample.token` and its `LIDAR_TOP` observations their
`sample_data.token` (held to the raw tables), content Scenes compose beside RobotRun
Scenes, and sample views build over a mixed version. Two S6 changes meet S5 artifacts:
the sample-view label index is today an allowed downstream site of the source-dispatch
guard (`_label_index.resolve`, a RobotRun anchor compared with the Scene's source), so
re-anchoring labels must update or remove that entry; and a mixed version is the natural
input for the S6 "labels independent of the sample-view policy" check. No S5 gap blocks
S6.
