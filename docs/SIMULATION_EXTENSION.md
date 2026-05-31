# Simulation Extension

This document describes how SceneOps can connect to future simulation or counterfactual data generation workflows.

SceneOps does not need to implement a simulator directly. Its role is to manage the outputs of simulation as versioned datasets that can be validated and evaluated through the same pipeline system.

---

## Core idea

```text
real sensor dataset
  -> reconstruction / simulation system
  -> simulated or re-observed dataset version
  -> SceneOps dataset registry
  -> validation / profiling
  -> model inference
  -> evaluation
  -> model comparison
```

This makes SceneOps the data/model/evaluation control plane around robotics simulation outputs.

---

## Target dataset types

```text
real
simulated
counterfactual
re_observed
pseudo_labeled
```

---

## Target simulation output manifest

```json
{
  "schema_version": "sceneops.simulation_output_manifest.v1",
  "source_dataset_id": "nuscenes",
  "source_dataset_version": "v1.0-mini",
  "generated_dataset_id": "nuscenes-counterfactual",
  "generated_dataset_version": "collision-case-v0",
  "generation_type": "counterfactual",
  "simulation_engine": "basic-sim",
  "scenario": {
    "name": "vehicle_cut_in",
    "description": "Inserted vehicle cuts into ego lane"
  },
  "outputs": {
    "camera_root_uri": "...",
    "lidar_root_uri": "...",
    "annotation_root_uri": "...",
    "metadata_uri": "..."
  },
  "lineage": {
    "source_scene_ids": ["scene-0001"],
    "source_sample_ids": ["sample-001", "sample-002"]
  }
}
```

---

## Target workflow

```text
1. Register original real dataset version.
2. Run reconstruction/simulation externally.
3. Register generated dataset version in SceneOps.
4. Run dataset ingestion or import manifest job.
5. Run dataset validation and profiling.
6. Run model inference on generated dataset.
7. Compare model performance against original dataset.
```

---

## Why this is important

Robotics models often fail on rare or risky edge cases. Simulation and counterfactual generation can produce additional evaluation data, but those outputs need the same infrastructure discipline as real sensor data:

- dataset versioning
- artifact tracking
- validation
- quality profiling
- model evaluation
- metric comparison

SceneOps can become the layer that connects generated robotics data to model development and evaluation.

---

## High-impact first implementation

Do not start with a full simulator integration.

Start with this minimal feature:

```text
Register generated dataset version
  -> attach source dataset lineage
  -> attach simulation output manifest URI
  -> run the same validation/evaluation pipeline
```

This creates the portfolio story without over-expanding implementation scope.
