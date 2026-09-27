"""Test-support utilities for sceneops_analytics (SceneOps V2 Request 3.2).

Nothing here is domain code and nothing here is re-exported from
sceneops_analytics's top-level ``__init__`` -- it exists so future
external-format adapter test suites (in this package or, once one exists, a
dedicated LeRobot/RLDS adapter package) can share one deterministic
fixture-building implementation instead of each hand-rolling its own.
Production code must never import from this package.

Deliberately DB-free (SceneOps V2 Request 3.2C.1 §1): the persistent E2E
fixture bootstrap that reads/writes real Postgres + MinIO
(``bootstrap_e2e_fixtures``/``verify_e2e_fixture``/``ensure_e2e_fixture``)
lives in ``scripts/e2e/e2e_fixture_bootstrap.py``, not here, precisely so
this package never needs to depend on ``sceneops-db`` just to support E2E
setup. This module keeps only the deterministic, I/O-free golden-data
definitions/expectations.
"""

from .counting_artifact_store import CountingArtifactStore, IoStats
from .interop_dataset import (
    EPISODE_A_REV1,
    EPISODE_A_REV1_REF,
    EPISODE_A_REV2,
    EPISODE_A_REV2_REF,
    EPISODE_B,
    EPISODE_B_REF,
    INTEROP_DATASET_ID,
    INTEROP_DATASET_VERSION,
    INTEROP_EPISODE_SPECS,
    INTEROP_FEATURE_PROJECTION,
    ExpectedEpisode,
    ExpectedStep,
    InteropDatasetBootstrap,
    InteropEpisodeSpec,
    build_interop_entries,
    build_interop_test_dataset,
    compute_expected_interop_episodes,
)
from .scale_fixture import (
    DEFAULT_SCALE_LADDER,
    ScaledDatasetArtifacts,
    ScaleSpec,
    all_episode_keys,
    build_scaled_entries,
    episode_ref,
    episode_revisions,
    expected_action,
    expected_observation,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)

__all__ = [
    "EPISODE_A_REV1",
    "EPISODE_A_REV1_REF",
    "EPISODE_A_REV2",
    "EPISODE_A_REV2_REF",
    "EPISODE_B",
    "EPISODE_B_REF",
    "INTEROP_DATASET_ID",
    "INTEROP_DATASET_VERSION",
    "INTEROP_EPISODE_SPECS",
    "INTEROP_FEATURE_PROJECTION",
    "DEFAULT_SCALE_LADDER",
    "CountingArtifactStore",
    "ExpectedEpisode",
    "ExpectedStep",
    "InteropDatasetBootstrap",
    "InteropEpisodeSpec",
    "IoStats",
    "ScaleSpec",
    "ScaledDatasetArtifacts",
    "all_episode_keys",
    "build_interop_entries",
    "build_interop_test_dataset",
    "build_scaled_entries",
    "compute_expected_interop_episodes",
    "episode_ref",
    "episode_revisions",
    "expected_action",
    "expected_observation",
    "feature_projection_for",
    "write_scaled_dataset_artifacts",
]
