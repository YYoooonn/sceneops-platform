# Derived Layer (L3)

Canonical Scenes and Episodes are source-faithful and asynchronous
([Scene domain](./scene-domain.md), [Episode domain](./episode-domain.md)).
Everything that needs synchronization, labels, selection, predictions or
learning tables is a *derived revision* built from pinned canonical input.
This is the authoritative description of that layer; the decisions behind it
are in ADR-007 §33.

```text
Scene   --labels-->  LabelSet revision  --+
Scene   --policy-->  SceneSampleView  <---+   pins Scene revision + label revisions
                          |
                    ScenarioSet revision      pins views
                          |
                    prediction revision       pins views, ScenarioSet, config
                          |
                    evaluation                pins prediction revision + label revision

Episode --config-->  AlignedEpisode revision --> learning export (explicit config)
```

## 1. Revisions and pins

A derived manifest is immutable. Its identity is the checksum of its canonical
JSON bytes; it is stored write-once at `manifest-<hex>.json`; its
ArtifactRecord id is a deterministic function of `(logical id, checksum)`
(`sceneops_core.common.derived_ids`). Registering identical work again is a
no-op; a conflicting duplicate fails. A consumer resolves a pin by verifying
the record's kind, owner and checksum and hashing the bytes
(`sceneops_worker.derived`). Records that do not pin a revision are refused
(`LegacyDerivedRecordError`) and must be rebuilt. There is no "latest" pointer.

## 2. Label sets

`LabelSetManifest` (`sceneops.label_set/v1`): provenance (`human`, `external`,
`model`), `coverage` (every annotated anchor, with or without objects) and
`Box3DLabel`s. An `ObservationAnchor` is
`(robot_run_id, channel, source_clock, timestamp_ns)`.

`IMPORT_LABELS` reads an adapter-produced document from the raw-source store,
checks that the anchored RobotRuns are registered, and registers a revision.
Labels never enter a Scene, Episode or recording. Source-format adapters live
outside the platform (`dataset-acquisition nuscenes-labels`).

## 3. Scene sample views

`BUILD_SCENE_SAMPLE_VIEWS` derives one `SceneSampleViewManifest` per Scene
revision from an explicit policy:

```text
anchor   { channel, stride }
members  [{ channel, association: nearest|previous, tolerance_ns, required }]
pose     { parent_frame_id, child_frame_id, association, tolerance_ns, required }
```

Association never crosses clocks and never interpolates; nearest ties go to
the earlier timestamp. Anchors that cannot satisfy a required member are
dropped with a recorded reason. A view stores references and deltas, not
payloads. Labels attach through the observations a sample holds, each labelled
observation owned by the nearest sample, so a label is counted once
(`label_stats`).

## 4. ScenarioSet

`scenario_curation` (`mine_scenarios` → `score_scenario_readiness`) takes
pinned sample views and explicit criteria (`label_set`, label count bounds,
`required_channels`, Scene readiness, sort, limit) and publishes a
`ScenarioSetManifest` whose members pin a view and name the selected samples.
Label criteria require a label set and every view must pin that revision.
`ScenarioSetRecord` projects exactly one revision.

## 5. Inference and evaluation

`detection_evaluation` (`predict_detection` → `evaluate_detection`):

- `predict_detection` takes a ScenarioSet or explicit pinned views, with
  explicit `camera_channel` and optional `lidar_channel`, and publishes a
  `DetectionPredictionManifest` with checksum-pinned per-sample shards.
  `InferenceRunRecord.prediction_manifest_checksum` pins it.
- Lidar payloads are decoded by declared media type
  (`application/x.ros2-cdr.sensor_msgs.msg.pointcloud2`); unknown types are a
  recorded failed lift. Lifted boxes carry the frame they are expressed in.
- `evaluate_detection` pins one prediction revision and one label revision,
  scores only samples the label set covers, and fails on frame mismatch.
  `categories` is an exact allowlist. The evaluation manifest records its
  `inputs`.
- The `mock` backend is a seeded test double over the labels attached to a
  sample; real backends never receive labels.

## 6. AlignedEpisode and learning export

`aligned_episode_building` (`align_episode` → `validate_aligned_episode` →
`profile_aligned_episode`) derives an aligned artifact from one Episode with an
explicit alignment config. The recipe key covers the full config, the
semantics version and the alignment clock; the artifact is write-once at its
key and its ArtifactRecord id derives from the Episode id and checksum. Its
DatasetVersion scope is the Episode's. The canonical Episode is not modified.

`EXPORT_LEARNING_DATA` exports pinned aligned artifacts with an explicit
`LearningDataExportConfig`; the export id is a content hash and its records are
deterministic. LeRobot and other external formats are downstream outputs of a
pinned export.

## 7. Current limitations

- Association is nearest / previous only; there is no pose interpolation.
- Evaluation applies no frame transform; a prediction and label in different
  frames fail.
- Labels are 3-D boxes anchored on Scene observations; Episodes have no label
  sets.
- The GroundingDINO backend path was not exercised by the real-data vertical
  (mock backend only).
- Validated on one nuScenes mini scene (`scene-0061`).
