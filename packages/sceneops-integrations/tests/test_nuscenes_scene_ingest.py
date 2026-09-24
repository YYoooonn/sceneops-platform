"""Tests for the nuScenes direct-SceneManifest INGEST capability (SceneOps
V2 Request 4.6B): ``sceneops_integrations.nuscenes.scene_ingest`` and
``runtime.py``'s ``mode="scene_manifest"`` dispatch.

As of Request 4.6B, ``nuscenes-devkit`` is no longer installed in the base
workspace venv (apps/worker no longer depends on it) -- this whole module
is skipped, not failed, wherever the SDK isn't present (same convention as
``test_nuscenes_runtime.py``).

Covers:
- ingest_nuscenes_scenes: source_scene_ids filters before max_source_scenes
  truncates (same order the migrated worker code always used); each scene
  is written to {scene_manifest_root_uri}/{scene_id}.json;
- runtime.execute(mode="scene_manifest"): requires scene_manifest_root_uri,
  produced_artifacts keyed "scene_manifest:<scene_id>" with scene_id in
  each ArtifactRef's own metadata, result_metadata aggregates sample/frame
  counts and channels across scenes;
- runtime.execute() still defaults to mode="raw_log" when config["mode"]
  is absent (no behavior change for existing raw-log callers);
- an unsupported config["mode"] is rejected clearly;
- the real /data/raw/nuscenes v1.0-mini fixture produces real
  ground-truth-annotated SceneManifests end-to-end (skipped, not failed,
  if that fixture isn't present).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

nuscenes = pytest.importorskip("nuscenes")

from sceneops_core.artifacts.schemas import ArtifactKind  # noqa: E402
from sceneops_core.datasets.schemas.external import ExternalDatasetRef  # noqa: E402
from sceneops_core.integration_runtime import (  # noqa: E402
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)
from sceneops_storage.backends.local import LocalArtifactStore  # noqa: E402

from sceneops_integrations.nuscenes import IntegrationRuntimeError, execute  # noqa: E402
from sceneops_integrations.nuscenes.scene_ingest import ingest_nuscenes_scenes  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_NUSCENES_DATAROOT = REPO_ROOT / "data" / "raw" / "nuscenes"
_HAS_REAL_FIXTURE = (REAL_NUSCENES_DATAROOT / "v1.0-mini").exists()


def _request(*, config: dict | None = None) -> IntegrationRequest:
    return IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
        config=config or {},
    )


def _mock_scene(name: str, token: str) -> dict:
    return {"name": name, "token": token, "first_sample_token": "", "description": ""}


def _mock_nusc(scenes: list[dict]) -> MagicMock:
    mock_nusc = MagicMock()
    mock_nusc.scene = scenes
    mock_nusc.get.side_effect = lambda kind, token: {"next": ""}
    return mock_nusc


# ── ingest_nuscenes_scenes: filtering/ordering ──────────────────────────────


class TestIngestNuscenesScenesFiltering:
    @pytest.mark.asyncio
    async def test_source_scene_ids_filters_before_max_source_scenes(
        self, tmp_path: Path
    ) -> None:
        scenes = [_mock_scene(f"scene-{i:04d}", f"tok-{i}") for i in range(5)]
        store = LocalArtifactStore(root_uri=str(tmp_path))

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc(scenes)):
            ingested = await ingest_nuscenes_scenes(
                artifact_store=store,
                source_root_uri="/data/raw/nuscenes",
                source_format_version="v1.0-mini",
                dataset_id="d",
                dataset_version="v1",
                scene_manifest_root_uri=str(tmp_path / "scenes"),
                source_scene_ids=["scene-0001", "scene-0002", "scene-0004"],
                max_source_scenes=2,
            )

        assert [s.scene_id for s in ingested] == ["scene-0001", "scene-0002"]

    @pytest.mark.asyncio
    async def test_no_filters_ingests_all_scenes_up_to_max(
        self, tmp_path: Path
    ) -> None:
        scenes = [_mock_scene(f"scene-{i:04d}", f"tok-{i}") for i in range(3)]
        store = LocalArtifactStore(root_uri=str(tmp_path))

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc(scenes)):
            ingested = await ingest_nuscenes_scenes(
                artifact_store=store,
                source_root_uri="/data/raw/nuscenes",
                source_format_version="v1.0-mini",
                dataset_id="d",
                dataset_version="v1",
                scene_manifest_root_uri=str(tmp_path / "scenes"),
            )

        assert [s.scene_id for s in ingested] == [
            "scene-0000",
            "scene-0001",
            "scene-0002",
        ]

    @pytest.mark.asyncio
    async def test_each_scene_written_under_root_uri(self, tmp_path: Path) -> None:
        scenes = [_mock_scene("scene-0001", "tok-1")]
        store = LocalArtifactStore(root_uri=str(tmp_path))
        root = str(tmp_path / "scenes")

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc(scenes)):
            ingested = await ingest_nuscenes_scenes(
                artifact_store=store,
                source_root_uri="/data/raw/nuscenes",
                source_format_version="v1.0-mini",
                dataset_id="d",
                dataset_version="v1",
                scene_manifest_root_uri=root,
            )

        assert ingested[0].manifest_uri == f"{root}/scene-0001.json"
        assert await store.exists(ingested[0].manifest_uri)


# ── runtime.execute mode dispatch ────────────────────────────────────────────


class TestRuntimeModeDispatch:
    @pytest.mark.asyncio
    async def test_scene_manifest_mode_requires_root_uri(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))
        with pytest.raises(IntegrationRuntimeError, match="scene_manifest_root_uri"):
            await execute(
                _request(config={"mode": "scene_manifest"}),
                artifact_store=store,
            )

    @pytest.mark.asyncio
    async def test_unsupported_mode_rejected(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))
        with pytest.raises(IntegrationRuntimeError, match="mode"):
            await execute(
                _request(config={"mode": "not-a-real-mode"}),
                artifact_store=store,
            )

    @pytest.mark.asyncio
    async def test_default_mode_is_raw_log(self, tmp_path: Path) -> None:
        """No config["mode"] set -- unchanged raw-log behavior (Request
        4.4-4.6), not a breaking default change."""
        store = LocalArtifactStore(root_uri=str(tmp_path))
        mock_nusc = MagicMock()
        mock_nusc.scene = []

        with patch("nuscenes.nuscenes.NuScenes", return_value=mock_nusc):
            result = await execute(
                _request(),
                artifact_store=store,
                raw_log_id="log-1",
                manifest_uri=str(tmp_path / "raw_log.json"),
                frame_index_uri=str(tmp_path / "frames.json"),
            )

        assert set(result.produced_artifacts) == {
            "raw_log_manifest",
            "raw_log_frame_index",
        }

    @pytest.mark.asyncio
    async def test_scene_manifest_mode_produces_artifacts_keyed_by_scene_id(
        self, tmp_path: Path
    ) -> None:
        scenes = [
            _mock_scene("scene-0001", "tok-1"),
            _mock_scene("scene-0002", "tok-2"),
        ]
        store = LocalArtifactStore(root_uri=str(tmp_path))

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc(scenes)):
            result = await execute(
                _request(config={"mode": "scene_manifest"}),
                artifact_store=store,
                scene_manifest_root_uri=str(tmp_path / "scenes"),
            )

        assert set(result.produced_artifacts) == {
            "scene_manifest:scene-0001",
            "scene_manifest:scene-0002",
        }
        for scene_id, ref in (
            ("scene-0001", result.produced_artifacts["scene_manifest:scene-0001"]),
            ("scene-0002", result.produced_artifacts["scene_manifest:scene-0002"]),
        ):
            assert ref.kind == ArtifactKind.SCENE_MANIFEST
            assert ref.metadata["scene_id"] == scene_id
            assert ref.checksum is not None

        assert result.result_metadata["scene_count"] == 2
        assert set(result.result_metadata["scene_ids"]) == {"scene-0001", "scene-0002"}


# ── real nuScenes fixture (skipped if not present) ──────────────────────────


@pytest.mark.skipif(
    not _HAS_REAL_FIXTURE,
    reason=f"real nuScenes v1.0-mini fixture not found at {REAL_NUSCENES_DATAROOT}",
)
class TestRealNuScenesFixture:
    @pytest.mark.asyncio
    async def test_real_scenes_have_ground_truth_annotations(
        self, tmp_path: Path
    ) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))
        ingested = await ingest_nuscenes_scenes(
            artifact_store=store,
            source_root_uri=str(REAL_NUSCENES_DATAROOT),
            source_format_version="v1.0-mini",
            dataset_id="test-e2e-core",
            dataset_version="test-v1",
            scene_manifest_root_uri=str(tmp_path / "scenes"),
            max_source_scenes=1,
        )

        assert len(ingested) == 1
        manifest = ingested[0].manifest
        assert manifest.has_ground_truth is True
        assert manifest.ground_truth_source == "nuscenes"
        assert manifest.annotation_count > 0
        assert manifest.sample_count > 0
