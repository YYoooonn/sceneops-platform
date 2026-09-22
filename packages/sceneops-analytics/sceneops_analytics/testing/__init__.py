"""Test-support utilities for sceneops_analytics (SceneOps V2 Request 3.2).

Nothing here is domain code and nothing here is re-exported from
sceneops_analytics's top-level ``__init__`` -- it exists so future
external-format adapter test suites (in this package or, once one exists, a
dedicated LeRobot/RLDS adapter package) can share one deterministic
fixture-building implementation instead of each hand-rolling its own.
Production code must never import from this package.
"""

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
    build_interop_test_dataset,
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
    "ExpectedEpisode",
    "ExpectedStep",
    "InteropDatasetBootstrap",
    "InteropEpisodeSpec",
    "build_interop_test_dataset",
]
