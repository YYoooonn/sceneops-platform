# Foundation Test Matrix

> Actual verification status as of Stabilization Request 8, not intended
> coverage. A cell reflects what was actually run and observed — see the
> notes under each row for the specific evidence. "Lower layer touched it"
> does not earn a `PASS` on its own; each column is judged independently.

## Legend

| State | Meaning |
| --- | --- |
| `PASS` | Directly verified, passing, on this branch |
| `PARTIAL` | Some real coverage exists at this layer, but not complete for this capability |
| `OPTIONAL-PASS` | Verified, but only reachable through an optional runtime (ROS2/Airflow/real inference) |
| `NOT-RUN` | Not exercised at this layer in this verification pass (not necessarily broken — just unverified here) |
| `N/A` | This layer doesn't apply to this capability |

## Columns

| Column | What it means |
| --- | --- |
| Unit | `make test` — infrastructure-independent |
| Service/API | API-layer test (fake-repository service test, or a live HTTP call against a running stack) |
| DB/Storage | `make test-integration` — real Postgres/MinIO |
| Job/Pipeline | A real Job/PipelineRun dispatched against the live worker |
| Default E2E | One of `make e2e`'s 9 default-stack scripts |
| Optional/Real | ROS2 / Airflow / real GroundingDINO inference — optional runtimes |
| Clean Room | Verified from an empty Postgres/MinIO/Redis state (Stabilization Request 7) |
| Restart | Verified to survive an app-only restart and/or a full `local-down`/`local-up` cycle (Stabilization Request 8) |

## Matrix

| Capability | Unit | Service/API | DB/Storage | Job/Pipeline | Default E2E | Optional/Real | Clean Room | Restart |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DatasetVersion | PASS | PASS | PASS | PASS | PASS | N/A | PASS | PASS |
| Scene build | PASS | N/A | PASS | PASS | PASS | N/A | PASS | PASS |
| Scene registration | PASS | PASS | PASS | PASS | PASS | N/A | PASS | PASS |
| Scene validation/profile | PASS | PASS | PASS | PASS | PASS | N/A | PASS | PASS |
| Scene API | PASS | PASS | PARTIAL | N/A | PASS | N/A | PASS | PASS |
| Raw-log isolation | PASS | N/A | PASS | PASS | PARTIAL | N/A | PASS | NOT-RUN |
| Episode build | PASS | N/A | PASS | PASS | PASS | OPTIONAL-PASS | PASS | PASS |
| Episode segmentation | PASS | N/A | N/A | PASS | PARTIAL | N/A | PASS | NOT-RUN |
| Episode registration | PASS | PASS | PASS | PASS | PASS | OPTIONAL-PASS | PASS | PASS |
| Episode validation/profile | PASS | PASS | PASS | PASS | PASS | OPTIONAL-PASS | PASS | PASS |
| Episode API | PASS | PASS | PARTIAL | N/A | PASS | N/A | PASS | PASS |
| RobotRun / MCAP | PASS | PASS | PARTIAL | PASS | N/A | OPTIONAL-PASS | PASS | PARTIAL |
| Detection prediction/evaluation | PASS | PASS | PARTIAL | PASS | PASS | OPTIONAL-PASS | PASS | NOT-RUN |
| Artifact lineage | PASS | PASS | PASS | PASS | PASS | N/A | PASS | PASS |
| Run records | PASS | PASS | PASS | PASS | PASS | N/A | PASS | PASS |
| Pipeline contracts | PASS | PASS | N/A | PASS | PASS | OPTIONAL-PASS | PASS | N/A |
| Airflow backend | NOT-RUN | PASS | N/A | PASS | N/A | OPTIONAL-PASS | NOT-RUN | NOT-RUN |
| Inference server | N/A | PASS (mock backend) | N/A | PASS (mock backend) | PASS (mock) | OPTIONAL-PASS | PASS (mock) | NOT-RUN |

## Notes on `PARTIAL` / `NOT-RUN` cells

- **Scene API / Episode API — DB/Storage: `PARTIAL`.** `sceneops-db/tests` has dedicated repository integration tests for `SceneRecord`/`EpisodeRecord`/`ArtifactRecord`/run-records/`DatasetVersion` (real Postgres), but no test specifically drives the API-layer quality-response builder against real Postgres — that path is covered by fake-repository unit tests (`Unit` column) plus live E2E calls (`Default E2E`), not a dedicated integration test.
- **Raw-log isolation — Default E2E: `PARTIAL`, Restart: `NOT-RUN`.** The default `raw-log-scene-building` E2E script only builds one raw log per run, so it doesn't exercise the multi-raw-log isolation invariant itself — that was verified separately with a real, multi-raw-log dispatch against live MinIO (Stabilization Request 7 §10). That specific two-raw-log dataset was not recreated and re-checked after a restart in Request 8; general MinIO/Postgres persistence *was* proven (episode manifest ETag identical pre/post restart, Request 8 §10), but this exact invariant wasn't independently re-probed post-restart.
- **Episode segmentation — Default E2E: `PARTIAL`, Restart: `NOT-RUN`.** The default suite's `episode-building` script only exercises `MISSION_BOUNDARY`. `WHOLE_RUN`, `FIXED_WINDOW`, and multiple-dated-missions were verified via direct pipeline dispatch (Request 7 §12) and unit tests, not the default suite, and not specifically re-checked after a restart.
- **RobotRun / MCAP — DB/Storage: `PARTIAL`, Restart: `PARTIAL`.** No dedicated repository integration test exists for `Robot`/`RobotRun`/`Mission`/`RobotState` (only `test_migration_schema.py`'s generic table/column existence check touches these tables at the schema level). Restart persistence was confirmed at the row-count level (`robot_runs` count unchanged across `local-down`/`local-up`) but not with the same field-by-field API-response diff used for Scene/Episode.
- **Detection prediction/evaluation — DB/Storage: `PARTIAL`, Restart: `NOT-RUN`.** Same gap as RobotRun: no dedicated `InferenceRun`/`EvaluationRun` repository integration test. Restart persistence for this capability specifically wasn't re-probed in Request 8 (no inference/evaluation run was part of the recorded persistence-probe set).
- **Airflow backend — Unit: `NOT-RUN`, Clean Room: `NOT-RUN`, Restart: `NOT-RUN`.** Airflow has no dedicated unit-test suite (it's exercised through live E2E only). It was deliberately excluded from Stabilization Request 7's clean-room verification (explicitly deferred to Request 8 per that request's §21) and wasn't part of the restart-persistence probe set in Request 8 — its own DAG state lives in a fully separate Postgres instance, outside this project's persistence scope.
- **Detection prediction/evaluation / Inference server — Optional/Real: `OPTIONAL-PASS`.** See [§Optional runtimes](#optional-runtimes) for the full story — the first build attempt failed on an environment prerequisite (host disk space exhausted mid-build), not a repository defect, but a real bug surfaced along the way and was fixed (`e2e_dataset_scene_ingestion.sh` never set `DatasetVersion.raw_source_root_uri`, which the real GroundingDINO backend requires but the mock backend doesn't). After the fix, real CPU inference against the live inference-server passed end-to-end.

## Optional runtimes

See the Request 8 report for the live results of each optional runtime
(ROS2/CAN-replay, Airflow, real inference-server) — summarized here:

- **ROS2 / real MCAP**: `OPTIONAL-PASS`. Real `ros2 bag record` produced a genuine MCAP file; `RosbagAdapter` decoded it; both `ingest_robot_states` and Episode build (`raw_log_episode_building`) consumed it successfully.
- **Airflow backend**: `OPTIONAL-PASS`. `dataset_scene_ingestion` dispatched through the Airflow REST API, all 6 tasks succeeded, `ExecutionRecord.execution_backend=airflow` recorded correctly.
- **Real inference-server (GroundingDINO)**: `OPTIONAL-PASS`. First build attempt failed during image export with a Docker/containerd I/O error, traced to host disk space exhaustion (~8.9GB free at the time) — an environment prerequisite, not a repository defect (Docker Desktop's daemon itself became briefly unresponsive as a result; recovered after a user-initiated restart, with all persisted data intact — see the Request 8 report §8-11). Root-cause investigation found and fixed a real, small Dockerfile bug along the way (`apps/inference-server/Dockerfile`'s CPU branch installed plain `torch` with no `--index-url`, pulling the full CUDA-bundled wheel even for the CPU path). Once disk space recovered, the rebuild succeeded and the server started healthy. Running the real E2E then surfaced a second, more consequential real bug: `scripts/e2e/e2e_dataset_scene_ingestion.sh` never set `DatasetVersion.raw_source_root_uri` (only `e2e_raw_log_scene_building.sh` did), which `predict_detection`'s real GroundingDINO backend requires to resolve sample image files — the mock backend doesn't need it, so this had never surfaced before. This meant the exact flow README's Demo 2 documents (`dataset-ingestion` -> `scenario-curation` -> `detection-evaluation-real`) was broken for any freshly ingested dataset version. Fixed by adding the missing `upsert_dataset_version` call to `e2e_dataset_scene_ingestion.sh`, matching the pattern already used in `e2e_raw_log_scene_building.sh`. After both fixes, real CPU GroundingDINO inference passed end-to-end at both full scope (10 scenes/500 samples: precision=0.318803, matching the value already documented in README's Demo 2) and minimal scope (1 scene/5 samples) — full artifact lineage and `scenario_set_id` propagation confirmed on both runs.

## Verified remaining limitations

Not stabilization failures — these define the boundary of the frozen
foundation, verified accurate as of Stabilization Request 8:

- `RobotRun`↔MCAP registration remains manual (no auto-binding of a freshly recorded MCAP to a `RobotRun`) — see [../workflows/robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md) §6.
- Camera/LiDAR binary payload materialization is incomplete — CDR-decoded but never written to the Artifact Store.
- Scenario Curation remains `experimental=True`; scenario candidates are artifact-backed only, no per-scenario DB row.
- `WORLD_STATE` remains reserved/unwired — see [../architecture/reserved-and-limitations.md](../architecture/reserved-and-limitations.md).
- Temporal synchronization/alignment between Episode observation/action channels is not implemented (`EpisodeManifestValidator`'s own docstring defers this explicitly).
- No Parquet/LeRobot-style canonical Episode export exists — Episode has no analytics-table equivalent of Scene's `export_analytics_snapshot`.
- The Airflow pipeline backend is a per-task DAG proof of concept hardcoded to `dataset_scene_ingestion` — no other pipeline type (including `raw_log_episode_building`) can be sent through it without generalizing the DAG.
- DuckDB queries only work against locally-downloaded Parquet files (no S3/MinIO httpfs support wired up).
