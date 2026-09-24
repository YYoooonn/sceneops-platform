"""Tests for IngestScenesJobHandler's nuScenes ingestion (SceneOps V2
Request 3.2B/3.2B.1 canonical/source version separation; migrated onto the
generic execution model in Request 4.6B).

Covers:
- dataset_version (canonical) vs. source_format_version (nuScenes SDK's
  own on-disk version) are never conflated -- no fallback either way;
- source_format_version is required for source_format=nuscenes, both at
  IngestScenesJobParams construction and (defense in depth) inside
  _ingest_nuscenes_scenes itself, before HttpIntegrationExecutor is ever
  invoked;
- _ingest_nuscenes_scenes builds its IntegrationRequest from real params
  (never a hardcoded source), runs it via HttpIntegrationExecutor against
  scenes_root_uri (not a raw-log URI), and reads each produced
  scene_manifest artifact back into a typed SceneManifest, keyed by the
  scene_id in each ArtifactRef's own metadata.
- the worker no longer imports nuscenes-devkit for this job at all
  (Request 4.6B) -- these tests mock HttpIntegrationExecutor, never the
  SDK.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.datasets.schemas import DatasetType
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationResult,
)
from sceneops_core.jobs.schemas import IngestScenesJobParams
from sceneops_core.scenes.schemas.manifests import SceneManifest

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


def _scene_manifest(scene_id: str) -> SceneManifest:
    return SceneManifest(
        scene_id=scene_id,
        dataset_id="test-e2e-core",
        dataset_version="test-v1",
        sample_count=3,
        frame_count=6,
        channels=["CAM_FRONT", "LIDAR_TOP"],
    )


def _make_context(
    *, scenes_root_uri: str = "s3://sceneops/artifacts/.../scenes"
) -> MagicMock:
    context = MagicMock()
    context.scene_artifact_store.scenes_root_uri = MagicMock(
        return_value=scenes_root_uri
    )
    context.scene_artifact_store.artifact_store.read_json = AsyncMock(
        side_effect=lambda uri: _scene_manifest(
            uri.rsplit("/", 1)[-1].removesuffix(".json")
        ).to_artifact_dict()
    )
    return context


def _integration_result(scene_ids: list[str]) -> IntegrationResult:
    return IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id="test-e2e-core", dataset_version="test-v1"
        ),
        produced_artifacts={
            f"scene_manifest:{scene_id}": ArtifactRef(
                kind=ArtifactKind.SCENE_MANIFEST,
                uri=f"s3://sceneops/artifacts/.../scenes/{scene_id}.json",
                metadata={"scene_id": scene_id},
            )
            for scene_id in scene_ids
        },
    )


# ── canonical/source version separation ──────────────────────────────────────


def test_changing_canonical_dataset_version_does_not_change_source_call() -> None:
    """Two params differing only in dataset_version must resolve to the
    identical nuScenes source version -- canonical identity is not an
    input to source loading at all."""
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
    call itself must still refuse to fall back to dataset_version -- before
    HttpIntegrationExecutor is ever invoked."""
    params = IngestScenesJobParams.model_construct(
        dataset_id="test-e2e-core",
        dataset_version="test-v1",
        source_format=DatasetType.NUSCENES,
        source_root_uri="/data/raw/nuscenes",
        source_format_version=None,
    )

    with patch(
        "sceneops_worker.integration_execution.HttpIntegrationExecutor"
    ) as MockExecutorClass:
        with pytest.raises(ValueError, match="source_format_version"):
            await _ingest_nuscenes_scenes(
                params=params, context=MagicMock(), job=MagicMock()
            )
    MockExecutorClass.assert_not_called()


# ── executor-based ingest (SceneOps V2 Request 4.6B) ────────────────────────


@pytest.mark.asyncio
async def test_uses_source_format_version_not_dataset_version_in_request() -> None:
    """The generic IntegrationRequest must carry the external source
    version, never the SceneOps canonical dataset_version."""
    params = _params(dataset_version="test-v1", source_format_version="v1.0-mini")
    context = _make_context()

    mock_executor_instance = AsyncMock()
    mock_executor_instance.execute = AsyncMock(
        return_value=_integration_result(["scene-0061"])
    )

    with patch(
        "sceneops_worker.integration_execution.HttpIntegrationExecutor",
        return_value=mock_executor_instance,
    ):
        await _ingest_nuscenes_scenes(params=params, context=context, job=MagicMock())

    request = mock_executor_instance.execute.call_args.args[0]
    assert request.external_ref.format_version == "v1.0-mini"
    assert request.canonical_ref.dataset_version == "test-v1"
    assert request.external_ref.format_version != request.canonical_ref.dataset_version


@pytest.mark.asyncio
async def test_request_uses_scene_manifest_mode_and_real_source_root() -> None:
    params = _params(source_root_uri="/data/raw/nuscenes")
    context = _make_context()

    mock_executor_instance = AsyncMock()
    mock_executor_instance.execute = AsyncMock(
        return_value=_integration_result(["scene-0061"])
    )

    with patch(
        "sceneops_worker.integration_execution.HttpIntegrationExecutor",
        return_value=mock_executor_instance,
    ):
        await _ingest_nuscenes_scenes(params=params, context=context, job=MagicMock())

    request = mock_executor_instance.execute.call_args.args[0]
    assert request.config["mode"] == "scene_manifest"
    assert request.external_ref.uri == "/data/raw/nuscenes"


@pytest.mark.asyncio
async def test_scene_manifest_root_uri_comes_from_scene_artifact_store() -> None:
    params = _params()
    context = _make_context(
        scenes_root_uri="s3://sceneops/artifacts/x/versions/y/scenes"
    )

    mock_executor_instance = AsyncMock()
    mock_executor_instance.execute = AsyncMock(
        return_value=_integration_result(["scene-0061"])
    )

    with patch(
        "sceneops_worker.integration_execution.HttpIntegrationExecutor",
        return_value=mock_executor_instance,
    ):
        await _ingest_nuscenes_scenes(params=params, context=context, job=MagicMock())

    context.scene_artifact_store.scenes_root_uri.assert_called_once_with(
        dataset_id="test-e2e-core", dataset_version="test-v1"
    )


@pytest.mark.asyncio
async def test_produced_scene_artifacts_read_back_into_typed_manifests() -> None:
    params = _params()
    context = _make_context()

    mock_executor_instance = AsyncMock()
    mock_executor_instance.execute = AsyncMock(
        return_value=_integration_result(["scene-0061", "scene-0103"])
    )

    with patch(
        "sceneops_worker.integration_execution.HttpIntegrationExecutor",
        return_value=mock_executor_instance,
    ):
        results = await _ingest_nuscenes_scenes(
            params=params, context=context, job=MagicMock()
        )

    scene_ids = {scene_id for scene_id, _, _ in results}
    assert scene_ids == {"scene-0061", "scene-0103"}
    for scene_id, uri, manifest in results:
        assert uri.endswith(f"{scene_id}.json")
        assert isinstance(manifest, SceneManifest)
        assert manifest.scene_id == scene_id
