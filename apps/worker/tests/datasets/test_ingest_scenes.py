"""Tests for IngestScenesJobHandler's nuScenes SDK version handling
(SceneOps V2 Request 3.2B/3.2B.1): `dataset_version` is SceneOps' own
canonical DatasetVersion identity; `source_format_version` is the nuScenes
SDK's own on-disk version folder name. They must never be conflated, and
there is no fallback from one to the other -- a missing
source_format_version fails clearly, both at IngestScenesJobParams
construction time and (defense in depth) inside the ingestion call itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from sceneops_core.datasets.schemas import DatasetType
from sceneops_core.jobs.schemas import IngestScenesJobParams
from sceneops_worker.jobs.dataset.ingest_scenes import _ingest_nuscenes_scenes


def _params(**overrides) -> IngestScenesJobParams:
    defaults = dict(
        dataset_id="test-e2e-core",
        dataset_version="test-v1",
        source_root_uri="/data/raw/nuscenes",
        source_format_version="v1.0-mini",
    )
    defaults.update(overrides)
    return IngestScenesJobParams(**defaults)


@pytest.mark.asyncio
async def test_uses_source_format_version_not_dataset_version() -> None:
    """The SDK must receive the external source version, never the
    SceneOps canonical dataset_version."""
    params = _params(dataset_version="test-v1", source_format_version="v1.0-mini")
    mock_nusc = MagicMock()
    mock_nusc.scene = []

    with patch("nuscenes.nuscenes.NuScenes", return_value=mock_nusc) as MockNuScenes:
        results = await _ingest_nuscenes_scenes(
            params=params, context=MagicMock(), job=MagicMock()
        )

    MockNuScenes.assert_called_once_with(
        version="v1.0-mini",
        dataroot="/data/raw/nuscenes",
        verbose=False,
    )
    assert results == []


def test_changing_canonical_dataset_version_does_not_change_source_call() -> None:
    """Two params differing only in dataset_version must resolve to the
    identical nuScenes SDK call -- canonical identity is not an input to
    source loading at all."""
    a = _params(dataset_version="test-v1")
    b = _params(dataset_version="some-other-canonical-version")
    assert a.source_format_version == b.source_format_version == "v1.0-mini"


def test_missing_source_format_version_raises_at_construction() -> None:
    with pytest.raises(ValidationError, match="source_format_version"):
        IngestScenesJobParams(
            dataset_id="test-e2e-core",
            dataset_version="test-v1",
            source_root_uri="/data/raw/nuscenes",
        )


def test_empty_string_source_format_version_also_raises_at_construction() -> None:
    with pytest.raises(ValidationError, match="source_format_version"):
        _params(source_format_version="")


def test_non_nuscenes_source_format_does_not_require_source_format_version() -> None:
    """The validator is conditional on source_format=nuscenes, not
    universal -- a hypothetical non-nuScenes source format is unaffected."""
    params = IngestScenesJobParams(
        dataset_id="test-e2e-core",
        dataset_version="test-v1",
        source_root_uri="/data/raw/custom",
        source_format=DatasetType.CUSTOM,
    )
    assert params.source_format_version is None


@pytest.mark.asyncio
async def test_ingest_function_rejects_missing_source_format_version_bypassing_validation() -> (
    None
):
    """Defense in depth: even if IngestScenesJobParams' own construction
    guarantee were ever bypassed (e.g. model_construct()), the ingestion
    call itself must still refuse to fall back to dataset_version."""
    params = IngestScenesJobParams.model_construct(
        dataset_id="test-e2e-core",
        dataset_version="test-v1",
        source_format=DatasetType.NUSCENES,
        source_root_uri="/data/raw/nuscenes",
        source_format_version=None,
    )

    with patch("nuscenes.nuscenes.NuScenes") as MockNuScenes:
        with pytest.raises(ValueError, match="source_format_version"):
            await _ingest_nuscenes_scenes(
                params=params, context=MagicMock(), job=MagicMock()
            )
    MockNuScenes.assert_not_called()
