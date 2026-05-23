### Target Architecture

```
Client / CLI / Dashboard
        ↓
FastAPI Control Plane
        ↓
Job / Run / Evaluation API
        ↓
Worker Pipeline
  ├── ingest
  ├── predict
  └── evaluate
        ↓
Metadata Store
  ├── Local JSON now
  └── PostgreSQL later
        ↓
Artifact Storage
  ├── Local files now
  └── MinIO / S3 / GCS later
        ↓
Model / MLOps Tools later
  ├── MLflow
  ├── ONNX Runtime
  ├── Triton
  ├── Prometheus
  └── Grafana
```

---

### folder tree

```
sceneops-platform/
  apps/
    api/        # FastAPI control plane
    worker/     # ingest / predict / evaluate worker
  data/
    nuscenes/   # nuScenes mini raw data
    manifests/  # dataset / scene / sample manifest
    artifacts/  # generated artifacts
    runs/       # inference / evaluation run outputs
  env/
    api.local.env
    worker.local.env
  infra/
    compose/    # later
    k8s/        # later
    helm/       # later
  Makefile
```

### design principle

```
API = control plane
Worker = execution plane
Repository = metadata backend abstraction
Storage = artifact backend abstraction
Run = model execution record
Evaluation = metric record
Job = orchestration command
```

---

### implement phases

#### 1. local-first pipeline

#### 2. job orchestration

#### 3. Metadata DB

#### 4. Object Storage

#### 5. Async worker

#### 6. Model/MLOps

#### 7. Monitoring/infra

---

### local commands

```bash
sceneops-worker
  ingest
    nuscenes
  predict
    mock-detection
  evaluate
    detection
```
