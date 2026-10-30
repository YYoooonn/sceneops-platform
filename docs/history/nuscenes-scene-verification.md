# nuScenes Scene verification (M3 S4)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: the Scene model and the nuScenes builder in
> [scene-domain.md](../architecture/scene-domain.md) §1 and §7, payload and clock rules in
> [ingress-boundaries.md](../architecture/ingress-boundaries.md) §4, the verifier's rules
> in [reference-verification.md](../development/reference-verification.md), the suites in
> [test-matrix.md](../development/test-matrix.md); decision:
> [ADR-015](../adr/015-multi-source-platform.md) §6 and its 2026-10-28 amendment;
> acceptance criteria: [multi-source-implementation-plan.md](./multi-source-implementation-plan.md)
> §4 (S4).

```text
date         2026-10-28
branch       refactor/multi-source-platform
HEAD         4a4757d (working tree: S4, uncommitted)
host         macOS 15.3.2 (Darwin 24.3.0), Apple M1 Pro, repository venv (Python 3.11)
docker       29.4.1; Docker Desktop VM filesystem 93.9 GB, 53–60 GB free during the runs;
             only the postgres / redis / minio servers of the local stack were started
             (the reference database was not migrated)
images       built here from the working tree before the final formatting pass:
             api f6ceb111a419, worker 59003b5206f5 (publish-snapshot ran as the
             host-side Publisher process)
inputs       data/raw/nuscenes (v1.0-mini), the nuscenes-scene-0061 declaration of
             config/reference/source-snapshots.json (2,989 files, 519,531,462 B);
             the locked scene-0061 recording (data/reference/nuscenes-mini-v1)
config       config/reference/nuscenes-scene-0061/scene_build_config.json (12 channels)
```

## 1. What was run

| Command | Result |
| --- | --- |
| `make test` | 2,717 passed, 120 skipped, 0 failed; the analytics refusal test added afterwards passed with its suite (158 passed, 3 skipped) |
| `make test-integration` (disposable database + bucket) | 323 passed (209 db + storage, 108 worker / analytics / execution incl. 6 content-Scene tests, 6 api) |
| `make test-infrastructure SUITE=nuscenes-scenes` (disposable runtime) | capacity preflight `ok` (3.87 GB required, 60.7 GB VM free); 9 passed in 117.6 s |
| `make test-infrastructure SUITE=pipelines` (recording regression, disposable runtime) | 25 passed in 309.8 s |
| `FIXTURE=scene-0061 canonical_bootstrap.sh` + `make reference-verify MODE=recording_import FIXTURE=scene-0061 DEEP=1` (disposable runtime) | 4,971 checks, 0 violations, 28 / 28 platform controls detected |
| `make e2e-scene-ml DISPOSABLE_RUNTIME=1` (recording Scenes, labels, sample views, mock detection, evaluation) | complete, 8 / 8 stages |
| `make reference-expectations WRITE=1` | only change: the recording Scene `semantics_version` 4 → 5 in all 10 fixtures |
| `make check`, `make lint`, `make check-boundaries`, `tools/checks/command_surface.py` | pass |
| `alembic downgrade -1` / `upgrade head` on an empty disposable database | `a9d4e7b2c6f3` ⇄ `f2c6a9e4b8d1` |
| `nuscenes_scene_verify.py derive`, `selftest` (no stack) | 1 buildable scene, 9 refused; 5,997 checks, 16 / 16 controls detected |

The first `reference-verify` attempt bootstrapped the default `smoke-1` scope, which
composes `baseline-smoke-1`; `FIXTURE=scene-0061` looks for `baseline-scene-0061`, so it
reported one `dataset_version_missing` violation. Bootstrapping with
`FIXTURE=scene-0061` composed the expected version and the run passed.

## 2. Results against the S4 acceptance criteria

| # | Criterion | Observed |
| --- | --- | --- |
| 1 | One Scene `cc8c0bf5…`: 2,963 observations on 12 channels (1,318 / 382 / 1,263), 39 keyframe groups of 12, one ego pose per observation | build result 2,963 observations, 39 keyframes, 2,963 poses; verifier `by_modality {camera 1318, lidar 382, radar 1263}`, group sizes `[12]`; every observation's `ego_pose_id` is `pose-<its ego_pose.token>` (verifier `observation_links`) |
| 2 | Window `[1532402927604844000, 1532402946826796001)`, `nuscenes.timestamp` / `unix_epoch`, 43.107 ms before the first sample and 29.279 ms after the last | as stated; `before_first_sample_ns` 43,107,000, `after_last_sample_ns` 29,279,000 |
| 3 | `SourceFileRef` sha256 equal the raw files, D3 media types, native refs, no CAN stream, no payload object | verifier `source_file_ref` / `scene_observations` / `keyframe_groups` pass against independently hashed files and raw tokens; every referenced object stored with its raw size; the builder plans no payload (unit test) and the build result carries no payload count (no object listing for `observation_payloads/` was taken) |
| 4 | Cross-source: camera timestamps and payload checksums equal the RobotRun Scene's; lidar timestamps equal | 224 `CAM_FRONT` (timestamp, checksum) pairs equal the RobotRun Scene's `/camera/front/image/compressed`; 382 `LIDAR_TOP` timestamps equal `/lidar/top/points` (numbers compared; the clock domains differ) |
| 5 | Without `unit_keys` the build fails naming the first unpreserved scene; `unit_keys` naming another scene fails | failed naming `scene-0757` (the first scene by token whose files are missing), and `scene-0103` for its own token; no object written |
| 6 | Validation, profiling, sample views and detection sample loading resolve through the one payload locator; rebuilding writes nothing | `validate_scene` `ready`, profile 2,963 / 39; `build_scene_sample_views` (anchor `LIDAR_TOP`, member `CAM_FRONT`) built one view; mock `predict_detection` loaded 5 samples through `PayloadLocator`; rebuild `created_manifest_count 0`, re-registration all `existing`, object listing and revision rows unchanged |

Execution provenance: a second declaration of the same bytes (`upstream_revision`
`v1.0-mini-b`) registered as `content: existing`, its build named snapshot B and reused
the manifest, whose ArtifactRecord kept the first build's Job. A `scene` source selector
over the content composed the same `composition_checksum` as the pins.

Measurements (`nuscenes_scene_measurements`): publish + register 36.4 s (2,989 files,
first registration hashes every file), pipeline 3.0 s, rebuild 2.1 s, re-register
1.1 s, verifier 10.0 s. The scene-0061 manifest is 2,931,237 B.

## 3. Findings

- **SceneManifest versioning.** The content variant is structural (union lineage and
  payloads, `native_ref`), so it ships as v4 rather than inside v3, and recording Scene
  manifests change bytes (`native_ref: null`). Recorded as the ADR-015 2026-10-28
  amendment.
- **Analytics.** `build_observations_table` read `payload.artifact_id` unconditionally
  and would have raised `AttributeError` on a content Scene; it now refuses one
  explicitly (`UnsupportedSceneContentError`).
- **Sample-view labels.** The label index read `lineage.source.robot_run_id`, which a
  content binding does not have; it now compares source identities, so a RobotRun
  label never resolves into a content Scene.
- **Stale active documents** found and corrected in passing: the ingress flow still
  said the snapshot build was not implemented, the Job list in data-model.md lacked the
  snapshot builds, and scene-domain.md named recording semantics version 3.

## 4. Not covered

- Mixed-source composition, the grep guards and the M3 golden expectations (S5); native
  nuScenes labels (S6).
- The `sources` and `lerobot-episodes` suites were not rerun; their code paths changed
  only by a renamed helper (`source_file_ref_mismatch`), which the content-Scene and
  content-Episode integration tests exercise.
- The migration's downgrade refusal with content-sourced rows present was not run.
- GroundingDINO lifting of a preserved `.pcd.bin` lidar reading (no decoder; a failed
  lift is recorded).
