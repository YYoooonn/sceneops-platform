# Roadmap

This roadmap is organized around portfolio impact for an AI Robotics Engineer / MLOps Data Infrastructure role.

---

## Current baseline

Implemented now:

- FastAPI control plane
- PostgreSQL metadata store
- Redis/Celery async execution
- Dataset registry and dataset version management
- Model registry and model version management
- Dataset ingestion pipeline
- Dataset manifest validation pipeline
- Mock detection inference
- ONNX Runtime dummy inference
- Center-distance detection evaluation
- Inference/evaluation run tracking
- Local artifact storage
- E2E scripts for dataset ingestion, mock detection, and ONNX detection

---

## Phase 1. Dataset quality and profiling

Goal:

```text
Turn dataset ingestion into a robotics sensor-data quality workflow.
```

Tasks:

- Add `profile_dataset` job type.
- Generate `dataset_profile_report.json` artifact.
- Track sensor completeness by channel.
- Track class distribution.
- Track empty annotation samples.
- Track sample-level annotation statistics.
- Add validation report artifact with pass/fail checks.
- Add blocked-pipeline reason when validation fails.

Why this is high impact:

- Directly matches robotics sensor data pipeline requirements.
- Makes the project look like data infrastructure, not just job orchestration.
- Creates a strong interview story around data quality gates.

---

## Phase 2. Model comparison and leaderboard

Goal:

```text
Turn inference/evaluation into a model iteration platform.
```

Tasks:

- Add evaluation comparison endpoint.
- Add detection leaderboard endpoint.
- Compare model versions on the same dataset version.
- Sort by precision, recall, or mean center distance error.
- Add model evaluation history view.

Why this is high impact:

- Shows that the platform supports model development loops.
- Connects data, model, and evaluation in a single workflow.
- Makes mock and ONNX Runtime backends useful as comparable runtime examples.

---

## Phase 3. Operational visibility

Goal:

```text
Make failed and slow pipelines easy to inspect.
```

Tasks:

- Add pipeline timeline API.
- Add job/pipeline metric summary API.
- Track queue latency.
- Track step duration.
- Track inference/evaluation duration.
- Add structured logs.

Why this is high impact:

- Directly supports monitoring and failure-response conversations.
- Makes the project feel closer to a production system.

---

## Phase 4. Inference serving abstraction

Goal:

```text
Prepare the model runtime for production serving backends.
```

Tasks:

- Add `external_http` inference backend.
- Add Triton backend contract.
- Define model repository layout.
- Separate preprocessing, inference, postprocessing, and export.
- Add inference server health checks.

Why this matters:

- Shows understanding of model serving architecture.
- Creates a bridge from batch validation to production inference.

---

## Phase 5. Object storage and artifact lineage

Goal:

```text
Make artifacts portable beyond local filesystem.
```

Tasks:

- Normalize artifact URI contract.
- Add artifact metadata table if needed.
- Add MinIO/S3-compatible storage backend.
- Track dataset, prediction, evaluation, and model artifacts by lineage.

Why this matters:

- Aligns with large-scale data infrastructure requirements.
- Makes the local-first architecture easier to explain as cloud-ready.

---

## Phase 6. Simulation / counterfactual dataset extension

Goal:

```text
Connect real2sim2real or counterfactual data generation outputs to the same validation/evaluation loop.
```

Tasks:

- Add `simulated` / `counterfactual` dataset type.
- Define simulation output manifest.
- Register synthetic/re-observed dataset versions.
- Validate simulated sensor outputs.
- Evaluate models on generated edge-case datasets.

Why this matters:

- Connects SceneOps with robotics simulation and data generation experience.
- Differentiates the project from generic MLOps examples.
