"""Tests for R2: input_store wired into WorkerContext.

Covers:
- create_worker_context produces a context with both artifact_store and input_store
- input_store is a distinct instance from artifact_store
- input_store is created from settings.input_source, not settings.artifact
- create_artifact_store called once per store type
- LocalArtifactStore root_uri reflects the correct setting
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sceneops_core.config import ArtifactSettings, InputSourceSettings
from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_worker.config import WorkerSettings
from sceneops_worker.core.dependencies import create_worker_context


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session() -> MagicMock:
    return MagicMock()


def _make_settings(
    *,
    artifact_root: str = "/data/artifacts",
    input_source_root: str = "/data/raw",
) -> WorkerSettings:
    return WorkerSettings(
        artifact=ArtifactSettings(root_uri=artifact_root),
        input_source=InputSourceSettings(root_uri=input_source_root),
    )


# ── WorkerContext field presence ──────────────────────────────────────────────


class TestWorkerContextFields:
    def test_artifact_store_exists(self) -> None:
        settings = _make_settings()
        ctx = create_worker_context(_make_session(), settings=settings)
        assert ctx.artifact_store is not None

    def test_input_store_exists(self) -> None:
        settings = _make_settings()
        ctx = create_worker_context(_make_session(), settings=settings)
        assert ctx.input_store is not None

    def test_artifact_store_and_input_store_are_distinct_instances(self) -> None:
        settings = _make_settings()
        ctx = create_worker_context(_make_session(), settings=settings)
        assert ctx.artifact_store is not ctx.input_store

    def test_settings_exposed_on_context(self) -> None:
        settings = _make_settings()
        ctx = create_worker_context(_make_session(), settings=settings)
        assert ctx.settings is settings


# ── Store roots reflect the right settings ───────────────────────────────────


class TestStoreRoots:
    def test_artifact_store_uses_artifact_root(self) -> None:
        settings = _make_settings(
            artifact_root="/data/artifacts",
            input_source_root="/data/raw",
        )
        ctx = create_worker_context(_make_session(), settings=settings)
        # LocalArtifactStore stores the root_uri
        assert isinstance(ctx.artifact_store, LocalArtifactStore)
        assert ctx.artifact_store.root_uri == "/data/artifacts"

    def test_input_store_uses_input_source_root(self) -> None:
        settings = _make_settings(
            artifact_root="/data/artifacts",
            input_source_root="/data/raw",
        )
        ctx = create_worker_context(_make_session(), settings=settings)
        assert isinstance(ctx.input_store, LocalArtifactStore)
        assert ctx.input_store.root_uri == "/data/raw"

    def test_different_roots_produce_different_stores(self) -> None:
        settings = _make_settings(
            artifact_root="/data/artifacts",
            input_source_root="/mnt/other/input",
        )
        ctx = create_worker_context(_make_session(), settings=settings)
        assert ctx.artifact_store.root_uri != ctx.input_store.root_uri  # type: ignore[union-attr]


# ── Factory call separation ───────────────────────────────────────────────────


class TestFactoryCalls:
    def test_create_artifact_store_called_for_artifact_settings(self) -> None:
        """create_artifact_store is called with settings.artifact."""
        import sceneops_worker.core.dependencies as dep_module

        settings = _make_settings()
        captured: list = []

        original = dep_module.create_artifact_store

        def spy(s):
            captured.append(s)
            return original(s)

        # Clear cached singletons to force fresh calls
        dep_module._artifact_store = None
        dep_module._input_store = None

        with patch.object(dep_module, "create_artifact_store", side_effect=spy):
            create_worker_context(_make_session(), settings=settings)

        roots = [s.root_uri for s in captured]
        assert "/data/artifacts" in roots
        assert "/data/raw" in roots

    def test_create_artifact_store_called_with_input_source_settings(self) -> None:
        import sceneops_worker.core.dependencies as dep_module

        settings = _make_settings(input_source_root="/custom/raw")
        captured: list = []

        original = dep_module.create_artifact_store
        dep_module._artifact_store = None
        dep_module._input_store = None

        def spy(s):
            captured.append(s)
            return original(s)

        with patch.object(dep_module, "create_artifact_store", side_effect=spy):
            create_worker_context(_make_session(), settings=settings)

        input_source_calls = [s for s in captured if s.root_uri == "/custom/raw"]
        assert len(input_source_calls) == 1
        assert input_source_calls[0] is settings.input_source
