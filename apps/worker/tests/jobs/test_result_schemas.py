"""Tests for run_id fields on ValidateSceneJobResult and ProfileSceneJobResult."""

from __future__ import annotations

from sceneops_core.jobs.schemas.results.scene import (
    ProfileSceneJobResult,
    ValidateSceneJobResult,
)


class TestValidateSceneJobResultRunId:
    def test_validation_run_id_default_none(self) -> None:
        result = ValidateSceneJobResult()
        assert result.validation_run_id is None

    def test_validation_run_id_set(self) -> None:
        result = ValidateSceneJobResult(validation_run_id="vrun-abc123")
        assert result.validation_run_id == "vrun-abc123"

    def test_serialization_includes_run_id(self) -> None:
        result = ValidateSceneJobResult(validation_run_id="vrun-abc123")
        data = result.model_dump()
        assert data["validation_run_id"] == "vrun-abc123"

    def test_serialization_run_id_none_when_unset(self) -> None:
        result = ValidateSceneJobResult()
        data = result.model_dump()
        assert data["validation_run_id"] is None


class TestProfileSceneJobResultRunId:
    def test_profile_run_id_default_none(self) -> None:
        result = ProfileSceneJobResult()
        assert result.profile_run_id is None

    def test_profile_run_id_set(self) -> None:
        result = ProfileSceneJobResult(profile_run_id="prun-def456")
        assert result.profile_run_id == "prun-def456"

    def test_serialization_includes_run_id(self) -> None:
        result = ProfileSceneJobResult(profile_run_id="prun-def456")
        data = result.model_dump()
        assert data["profile_run_id"] == "prun-def456"

    def test_serialization_run_id_none_when_unset(self) -> None:
        result = ProfileSceneJobResult()
        data = result.model_dump()
        assert data["profile_run_id"] is None
