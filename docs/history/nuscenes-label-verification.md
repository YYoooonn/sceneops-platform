# Native nuScenes label verification (M3 S6)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: label sets, anchor resolution and sample-view
> attachment in [derived-layer.md](../architecture/derived-layer.md) §2–§3 and
> [ADR-015](../adr/015-multi-source-platform.md) §9 (S6 amendment), the verifier's rules
> in [reference-verification.md](../development/reference-verification.md) ("Native
> nuScenes labels"), the suites in [test-matrix.md](../development/test-matrix.md);
> acceptance criteria: [multi-source-implementation-plan.md](./multi-source-implementation-plan.md)
> §4 (S6).

```text
date         2026-10-29
branch       refactor/multi-source-platform
HEAD         711f1fb (working tree: S6, uncommitted)
host         macOS 15.3.2 (Darwin 24.3.0), Apple M1 Pro, repository venv (Python 3.11)
docker       Docker Desktop VM, 53.9 GB free at the capacity preflight; the reference
             stack's postgres / redis / minio served the disposable databases and
             buckets; the reference database, bucket, API and workers were neither read
             nor written (their containers were not restarted)
images       built here from the working tree: worker 362286d89548, api 1170e94777f1
inputs       data/raw/nuscenes (v1.0-mini) selected by the nuscenes-scene-0061
             declaration; the locked reference labels and recording of scene-0061
             (data/reference/nuscenes-mini-v1); data/raw/lerobot/pusht (mixed suite)
```

## 1. What was run

| Command | Result |
| --- | --- |
| `make reference-verify-nuscenes-labels-selftest` | 41 checks, 0 violations, negative controls 18 / 18 detected |
| `make test-infrastructure SUITE=nuscenes-labels` (disposable runtime) | **7 passed in 99.9 s**; capacity preflight `ok` (3.87 GB required) |
| `make test-infrastructure SUITE=mixed-sources` (disposable runtime) | **13 passed in 362.3 s** (the 12 S5 tests and one S6 test) |
| `make e2e-scene-ml` on a disposable runtime (`disposable_env.py run --runtime`), then `export_analytics_snapshot` of its DatasetVersion twice | journey passed in 101 s; export succeeded (1 scene, 606 observations, 0 keyframes); the second export returned the same table URIs |
| integration (`*_integration.py`, disposable database + bucket): `test_native_label_import_integration.py`, `test_content_scene_integration.py`, `test_derived_vertical_integration.py` | 11 passed (4 new) |
| `make test` | 21 suites: 2,802 passed, 136 skipped, 0 failed (incl. `check-boundaries`, the source-dispatch guard and the verifier tests) |
| `make lint`, `make check-boundaries`, `make check-commands` | passed (9 infrastructure suites) |

`make test-integration` (the whole set) was not run; the integration modules above are the
ones whose code paths S6 changed or extends. The `e2e-scene-ml` driver and its export step
were scratch scripts, not repository code. `e2e-scene-ml` itself runs no analytics export;
the export was added to it here to cover the S5 export fix on a recording DatasetVersion.

## 2. Observations

**Raw derivation (independent verifier, stdlib only).** scene-0061 of the slice yields 39
anchors and 4,699 labels over 227 instances and 12 categories; every sample has
annotations; every sample's `LIDAR_TOP` key frame is at `sample.timestamp` and preserved.
Expected checksum `sha256:9efe1212…6abb16` for `label_set_id = nuscenes-scene-0061`, artifact
`labelset-1ed2db28df44d0aaeef4c36292fa7324`. The platform's pure importer on the same
selection produces exactly this checksum.

**Platform run (`SUITE=nuscenes-labels`).**

| Step | Observed |
| --- | --- |
| `import_labels {snapshot_id, label_set_id, unit_keys}` | 4,699 labels, 39 anchors, 2.1 s; result names the snapshot and content; `content_ids` = [content], `robot_run_ids` = [] |
| views | CAM_FRONT-anchored (LIDAR_TOP optional member): 224 samples, 4,699 attached; LIDAR_TOP-anchored (CAM_FRONT optional member): 382 samples, 4,699 attached; both 0 unattached |
| verifier `verify` | 41 checks, 0 violations; 18 / 18 controls detected; 6.5 s |
| re-import | same revision, `created = false`; no object written; label set bytes and the DatasetVersion's composition checksum unchanged by views |
| second snapshot of the same bytes | same revision, `created = false`, result names snapshot B, artifact record keeps the first Job |
| refusals | import without `unit_keys` fails (nine scenes' key frames are not preserved: "is not a file of"); DatasetVersion fields in native params are rejected at `POST /jobs`; nothing written |
| RobotRun label path | the native labels equal the locked reference labels of scene-0061 that `render-labels` renders: same 4,699 tokens, anchor timestamps, sample tokens, instances, categories, attributes and box values (frames are named after each Scene: `map` / `global`) |

**Mixed version.** On the S5 mixed DatasetVersion, a LIDAR_TOP view pinning the native
labels covers 39 anchors with 4,699 attached on the nuScenes Scene; a `/lidar/top/points`
view of the RobotRun Scene of the same nuScenes scene covers 0 anchors and attaches 0
labels, although its lidar readings carry the same timestamps (the anchor names the
content, not the RobotRun). Recomposing the version returns `created = false` with the
same checksum.

**Pure checks on the raw slice** (`test_native_labels_on_scenes.py`): without a view,
each of the 39 anchors resolves to one `LIDAR_TOP` observation inside the keyframe group
whose `native_ref` is its `group_ref` (39 distinct groups); CAM_FRONT- and LIDAR_TOP-anchored
views present identical label ids per group.

## 3. Negative controls

All are consistent faults (the label set re-serialized, checksum and artifact id
recomputed, the views re-pinned), each detected by its own code besides the checksum:
`swapped_group_ref` (`label_group_ref`, `label_resolution`), `anchor_timestamp_shift`
1 ns (`label_anchor_time`, `label_resolution`), `other_content_binding` and
`recording_binding` (`label_anchor_binding`), `lost_label` (`label_tokens`), `lost_sample`
(`label_coverage`, `label_tokens`), `label_on_another_sample`, `box_center` 1 cm,
`box_size_swap`, `box_rotation` (`label_geometry`), `box_frame` (`label_frame`),
`category_swap`, `instance_swap` (`label_semantics`), `snapshot_in_label_set`
(`label_set_forbidden_field`), `view_labels_on_wrong_group`, `view_lost_label`
(`view_group_labels`), `view_label_pin`, `scene_group_token_swap` (`label_resolution`).

## 4. Defects and findings

- No platform defect was found by the runs.
- The S5 record states that `e2e-scene-ml` exports analytics; it does not (no
  `export_analytics_snapshot` in the journey). The export was run explicitly here.

## 5. Limits of this evidence

- One nuScenes scene (`scene-0061`) of the slice snapshot; the full mini snapshot was not
  imported.
- Visibility, lidar / radar point counts and annotation prev / next links are not carried
  and so not verified.
- `test-integration` as a whole, the `sources`, `lerobot-episodes`, `nuscenes-scenes` and
  `pipelines` suites, `e2e-episode-learning`, `e2e-streaming-equivalence` and the clean
  room were not rerun.
- The reference environment's database is still not migrated past S2; it was not touched.
