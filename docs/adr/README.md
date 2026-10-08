# Architecture decision records

ADRs record why a decision was made. They are not rewritten when code moves, so the file
paths, module names and commands they cite are those of the time they were written. The
current layout is described in
[Repository structure](../architecture/repository-structure.md); this table translates the
paths a historical ADR or study names.

| Cited path or command | Now |
| --- | --- |
| `ros2/capture/`, `capture_consumer.py`, `mcap_writer.py` | `packages/sceneops-recording/sceneops_recording/capture/` (`consumer.py`, `writer.py`); process `apps/capture` |
| `ros2/nodes/streaming_bridge_node.py` | `integrations/ros2-kafka-bridge/sceneops_streaming_bridge/node.py` |
| `ros2/channels/` | `config/channels/` |
| `ros2` service / image, `make ros2-test` | `streaming-bridge` and `capture` services, `make streaming-test` |
| `sceneops_integrations.recording`, `python -m sceneops_integrations.recording` | `sceneops_recording`; the CLI is `python -m sceneops_publisher` (`apps/publisher`) |
| `sceneops_core.streaming`, `sceneops_core.constants.streaming` | `sceneops_streaming` |
| `sceneops_core.robots.{capture_receipt,capture_scan,published_scan,recovery_log}` | `sceneops_recording.*` |
| `sceneops_core.robots.{artifact_lifecycle,registration_failures}` | `sceneops_acquisition.lifecycle.vocabulary`, `sceneops_acquisition.registration_failures` |
| `app.domains.robots.{reconciliation,acquisition_status,artifact_lifecycle,registration}` | `sceneops_acquisition.{reconciliation,status,lifecycle,registration}` |
| `python -m app.domains.robots.reconciliation` (and `.artifact_lifecycle`, `.acquisition_status`) | `sceneops-worker acquisition reconcile` (`artifact-lifecycle`, `status`) |
| `SCENEOPS_API_RECONCILER__*`, `SCENEOPS_API_ARTIFACT_LIFECYCLE__*` | `SCENEOPS_WORKER_RECONCILER__*`, `SCENEOPS_WORKER_ARTIFACT_LIFECYCLE__*` |
| `app.platform.{jobs,pipelines,executions}.{service,dispatch_facade,schemas,backends}` | `sceneops_execution.{jobs,pipelines,executions}.*` |
| `sceneops_worker.{execution,jobs.lease*,jobs.events,pipelines.quality_gate,...}` | `sceneops_execution.*` |
| `sceneops_worker.{scenes,episodes}.{artifacts,recording_builder,profiling,validation}` | `sceneops_scenes.*`, `sceneops_episodes.*` |
| `sceneops_worker.recordings.{payloads,payload_store}` | `sceneops_recording.observations.{payloads,store}` |
| `sceneops_worker.derived.manifests`, `sceneops_worker.runs` | `sceneops_derived.manifests`, `sceneops_derived.run_artifacts` |
| `sceneops_worker.inference.detection.*`, `sceneops_worker.evaluation.*` | `sceneops_inference.detection.*`, `sceneops_evaluation.*` |
| `scripts/{checks,e2e,reference,canonical,streaming,dev,ops,init}/` | `tools/{checks,e2e,reference,baselines/canonical,baselines/streaming,dev}/` |
| `benchmarks/` | `tools/benchmarks/` |
| `apps/streaming-bridge` | `integrations/ros2-kafka-bridge` (an external ingress adapter, not a SceneOps app) |
| `apps/inference-server` | `integrations/groundingdino-server` |
| `tools/dataset-acquisition` | `integrations/dataset-acquisition` |
| `tools/lerobot-integration` | `integrations/lerobot` |
