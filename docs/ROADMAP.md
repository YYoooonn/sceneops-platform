# Roadmap

This roadmap is ordered by value for an AI Robotics Engineer / MLOps portfolio.

## Final target

Build a production-like MLOps platform for robotics perception workflows.

The platform should support:

```text
sensor dataset ingestion
  -> validation
  -> profiling
  -> model inference
  -> evaluation
  -> model comparison
  -> artifact lineage
  -> monitoring
  -> serving
```

## Phase 0. Documentation and repository clarity

Goal: make the current implementation understandable from the repository alone.

Tasks:

- Rewrite `README.md`
- Add `docs/ARCHITECTURE.md`
- Add `docs/E2E.md`
- Add `docs/DATASET_AND_ARTIFACTS.md`
- Add `docs/MODEL_AND_EVALUATION.md`
- Add `docs/OPERATIONS.md`
- Ensure Makefile targets match documentation
- Ensure docker-compose service names match documentation
- Ensure E2E scripts match API route contracts

Done when:

- A new reader can understand what the platform does in 5 minutes
- A new reader can run the local E2E workflows from README
- Current implementation and future roadmap are clearly separated

## Phase 1. Sensor dataset pipeline

Goal: make the dataset layer clearly aligned with robotics sensor data infrastructure.

Tasks:

- Improve sensor-centric dataset manifest
- Include scene/sample/sensor channel structure
- Track camera/LiDAR artifact URI
- Track timestamp
- Track ego pose
- Track calibration references
- Track annotation references
- Add dataset validation job
- Add dataset profiling job

Validation checks:

```text
required sensor channels exist
sensor files exist
timestamps are ordered
camera/LiDAR calibration exists
sample annotations are valid
class distribution is computable
```

Artifacts:

```text
dataset_manifest.json
validation_report.json
profile_report.json
```

Done when:

- `dataset_ingestion` pipeline produces a manifest, validation report, and profile report
- dataset version metadata points to generated artifacts
- invalid/missing sample cases are reported clearly

## Phase 2. Model runtime and inference contract

Goal: make model execution replaceable and production-like.

Tasks:

- Normalize model registry schema
- Normalize model version schema
- Define model artifact contract
- Define inference backend interface
- Split prediction into sample loading, preprocessing, inference, postprocessing, prediction manifest export
- Keep mock backend
- Strengthen ONNX Runtime backend
- Add realistic detection output contract

Backends:

```text
mock
onnx_runtime
triton later
```

Done when:

- A model version can determine backend and artifact URI
- Prediction code does not hardcode model behavior into the pipeline runner
- ONNX Runtime path follows the same contract as mock path

## Phase 3. Evaluation and comparison

Goal: turn the project from pipeline execution into model development support tooling.

Tasks:

- Add model-version evaluation history endpoint
- Add evaluation comparison endpoint
- Add detection leaderboard endpoint
- Add per-class metrics
- Add threshold-based metrics
- Add latency metrics
- Add throughput metrics

Target APIs:

```text
GET /api/v1/models/{model_id}/versions/{version}/evaluations
GET /api/v1/evaluations/compare
GET /api/v1/leaderboards/detection
```

Done when:

- Two model versions can be compared on the same dataset version
- Evaluation output is reproducible from stored metadata/artifacts
- Metrics are visible without manually opening files

## Phase 4. Artifact storage and lineage

Goal: make artifacts portable across local and object-storage environments.

Tasks:

- Normalize artifact URI format
- Add `local://` URI convention
- Add artifact metadata model if needed
- Add MinIO service to local compose
- Add S3-compatible storage implementation
- Store artifact size/checksum/content type
- Connect dataset, prediction, evaluation, and model artifacts through lineage

Target URI examples:

```text
local://datasets/nuscenes/v1.0-mini/manifest.json
local://runs/inference/{run_id}/predictions.json
local://runs/evaluations/{evaluation_run_id}/report.json
s3://sceneops/datasets/nuscenes/v1.0-mini/manifest.json
```

Done when:

- local and object storage can be swapped by config
- pipeline result exposes artifact URIs consistently
- artifacts are traceable back to pipeline/job/model/dataset metadata

## Phase 5. Observability and operations

Goal: show production-oriented system design.

Tasks:

- Add structured logs
- Add job duration metrics
- Add pipeline duration metrics
- Add step duration metrics
- Add queue latency metrics
- Add retry/failure metrics
- Add inference latency metrics
- Add API metrics endpoint
- Add Prometheus exporter
- Add Grafana dashboard

Initial metrics:

```text
sceneops_pipeline_total
sceneops_pipeline_duration_seconds
sceneops_job_total
sceneops_job_duration_seconds
sceneops_job_queue_latency_seconds
sceneops_inference_latency_seconds
sceneops_evaluation_duration_seconds
sceneops_job_failures_total
```

Done when:

- pipeline/job health can be inspected without reading raw logs
- failures and slow stages are visible
- local monitoring stack can be started from compose

## Phase 6. Serving

Goal: connect batch validation workflows with production inference serving.

Tasks:

- Add Triton service to compose
- Define Triton model repository layout
- Add Triton inference backend
- Add model server health check
- Add serving request path
- Track serving metrics separately from batch metrics

Done when:

- the same model version concept can point to ONNX Runtime batch inference or Triton serving
- serving health and latency are observable
- detection_validation pipeline can optionally call serving backend

## Phase 7. Robotics/simulation extension

Goal: connect the platform to robotics simulation and counterfactual data generation.

Tasks:

- Add simulated dataset type
- Add counterfactual dataset type
- Define simulation output manifest
- Define intervention metadata contract
- Add calibration validation for camera/LiDAR
- Add pseudo-label workflow placeholder
- Add VLM/VLA annotation workflow concept

Target flow:

```text
real dataset version
  -> reconstructed/simulated/counterfactual dataset version
  -> prediction
  -> evaluation
  -> comparison against real baseline
```

Done when:

- simulated/counterfactual outputs can be registered as dataset versions
- simulation artifacts can be evaluated through the same model pipeline
- the project connects clearly to robotics digital twin workflows
