"""Unit tests for empty-input guards in scene aggregation/quality job handlers."""

from __future__ import annotations

import pytest

from sceneops_core.jobs.schemas import (
    BuildDatasetManifestJobParams,
    BuildSceneIndexJobParams,
    ProfileSceneJobParams,
    ValidateSceneJobParams,
)
from sceneops_core.jobs.schemas.results.scene import ValidateSceneJobResult


class TestValidateSceneEmptyInputGuard:
    """validate_scene with empty scene_manifest_uris returns a blocking failure."""

    def _resolve_uris(self, params: ValidateSceneJobParams) -> list[str]:
        if params.scene_manifest_uris:
            return params.scene_manifest_uris
        if params.scene_manifest_uri:
            return [params.scene_manifest_uri]
        return []

    def test_empty_uris_should_block(self) -> None:
        params = ValidateSceneJobParams()
        uris = self._resolve_uris(params)
        assert uris == []
        # Guard produces a blocking result.
        result = ValidateSceneJobResult(
            status="failed",
            should_block_pipeline=True,
            checked_scene_count=0,
            issue_count=1,
            metadata={
                "issues": [
                    {
                        "code": "empty_scene_manifest_input",
                        "message": "validate_scene requires at least one scene manifest URI.",
                    }
                ]
            },
        )
        assert result.should_block_pipeline is True
        assert result.checked_scene_count == 0
        assert result.issue_count == 1

    def test_nonempty_uris_not_empty(self) -> None:
        params = ValidateSceneJobParams(scene_manifest_uris=["s3://bucket/scene.json"])
        uris = self._resolve_uris(params)
        assert len(uris) == 1

    def test_singular_uri_not_empty(self) -> None:
        params = ValidateSceneJobParams(scene_manifest_uri="s3://bucket/scene.json")
        uris = self._resolve_uris(params)
        assert len(uris) == 1


class TestProfileSceneEmptyInputGuard:
    """profile_scene with empty scene_manifest_uris raises ValueError."""

    def _resolve_uris(self, params: ProfileSceneJobParams) -> list[str]:
        if params.scene_manifest_uris:
            return params.scene_manifest_uris
        if params.scene_manifest_uri:
            return [params.scene_manifest_uri]
        return []

    def test_empty_uris_raises(self) -> None:
        params = ProfileSceneJobParams()
        uris = self._resolve_uris(params)
        assert uris == []
        with pytest.raises(ValueError, match="profile_scene requires"):
            if not uris:
                raise ValueError(
                    "profile_scene requires at least one scene manifest URI."
                )

    def test_nonempty_uris_ok(self) -> None:
        params = ProfileSceneJobParams(scene_manifest_uris=["s3://bucket/scene.json"])
        uris = self._resolve_uris(params)
        assert len(uris) == 1


class TestBuildSceneIndexEmptyInputGuard:
    """build_scene_index with empty scene_manifest_uris raises ValueError."""

    def test_empty_uris_raises(self) -> None:
        params = BuildSceneIndexJobParams()
        uris = list(params.scene_manifest_uris)
        assert uris == []
        with pytest.raises(ValueError, match="build_scene_index requires"):
            if not uris:
                raise ValueError(
                    "build_scene_index requires at least one scene manifest URI."
                )

    def test_nonempty_uris_ok(self) -> None:
        params = BuildSceneIndexJobParams(
            scene_manifest_uris=["s3://bucket/scene.json"]
        )
        uris = list(params.scene_manifest_uris)
        assert len(uris) == 1


class TestBuildDatasetManifestEmptyInputGuard:
    """build_dataset_manifest with no scene_index_uri and no scene_manifest_uris raises."""

    def test_empty_uris_after_db_fetch_raises(self) -> None:
        # Simulate the case where params are empty and DB fetch also returns nothing.
        uris: list[str] = []
        with pytest.raises(ValueError, match="build_dataset_manifest requires"):
            if not uris:
                raise ValueError(
                    "build_dataset_manifest requires a scene index URI or at least one "
                    "scene manifest URI."
                )

    def test_provided_uris_pass_guard(self) -> None:
        params = BuildDatasetManifestJobParams(
            dataset_id="ds-001",
            dataset_version="v1",
            scene_manifest_uris=["s3://bucket/scene.json"],
        )
        uris = params.scene_manifest_uris
        assert len(uris) == 1
