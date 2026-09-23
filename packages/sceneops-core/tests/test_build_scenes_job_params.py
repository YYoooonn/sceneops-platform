"""Tests for BuildScenesJobParams' source_format_version requirement
(SceneOps V2 Request 3.2B/3.2B.1): required whenever
source_type=nuscenes_raw_log_mock, with no fallback to dataset_version.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneops_core.jobs.schemas import BuildScenesJobParams
from sceneops_core.observations.schemas import RawLogSourceType


def test_missing_source_format_version_raises_for_nuscenes_raw_log_mock():
    with pytest.raises(ValidationError, match="source_format_version"):
        BuildScenesJobParams(
            dataset_id="test-e2e-raw-log",
            dataset_version="test-v1",
            source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
        )


def test_empty_string_source_format_version_also_raises():
    with pytest.raises(ValidationError, match="source_format_version"):
        BuildScenesJobParams(
            dataset_id="test-e2e-raw-log",
            dataset_version="test-v1",
            source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
            source_format_version="",
        )


def test_explicit_source_format_version_succeeds():
    params = BuildScenesJobParams(
        dataset_id="test-e2e-raw-log",
        dataset_version="test-v1",
        source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
        source_format_version="v1.0-mini",
    )
    assert params.source_format_version == "v1.0-mini"


def test_other_source_types_do_not_require_source_format_version():
    # RosbagAdapter (REAL_ROBOT_LOG) has no nuScenes-SDK dependency -- the
    # validator is conditional, not universal.
    params = BuildScenesJobParams(
        dataset_id="test-e2e-core",
        dataset_version="test-v1",
        source_type=RawLogSourceType.REAL_ROBOT_LOG,
    )
    assert params.source_format_version is None


def test_no_source_type_does_not_require_source_format_version():
    params = BuildScenesJobParams()
    assert params.source_type is None
    assert params.source_format_version is None
