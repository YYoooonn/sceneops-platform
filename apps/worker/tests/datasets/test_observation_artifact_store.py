"""Tests for ObservationArtifactStore raw-log-scoped path isolation.

SceneOps V2 Request 22 / F-01: raw-log-derived artifacts (RawLogManifest,
RawLogFrameIndex, SceneSegments) must be scoped by raw_log_id within a
DatasetVersion, not only by DatasetVersion — otherwise a second raw-log
build/ingestion against the same dataset version silently overwrites the
first raw log's artifacts.
"""

from __future__ import annotations

import pytest

from sceneops_core.observations.schemas.raw_logs import (
    RawLogFrameIndex,
    RawLogManifest,
)
from sceneops_core.observations.schemas.enums import RawLogSourceFormat
from sceneops_core.scenes.schemas.segments import SceneSegmentIndex
from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_worker.observations.artifacts import ObservationArtifactStore

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


def _store(tmp_path) -> ObservationArtifactStore:
    backend = LocalArtifactStore(root_uri=str(tmp_path))
    return ObservationArtifactStore(
        artifact_store=backend, dataset_root_uri=str(tmp_path)
    )


def _version_root(tmp_path) -> str:
    return str(tmp_path / DATASET_ID / "versions" / DATASET_VERSION)


def _manifest(raw_log_id: str) -> RawLogManifest:
    return RawLogManifest(
        raw_log_id=raw_log_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        dataset_type="rosbag",
        source_format=RawLogSourceFormat.ROSBAG,
        root_uri=f"s3://source/{raw_log_id}",
        channels=[f"CHANNEL_{raw_log_id}"],
    )


def _frame_index(raw_log_id: str) -> RawLogFrameIndex:
    return RawLogFrameIndex(
        raw_log_id=raw_log_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
    )


def _segment_index(raw_log_id: str) -> SceneSegmentIndex:
    return SceneSegmentIndex(
        raw_log_id=raw_log_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
    )


# ── URI builders: distinct raw_log_id -> distinct URIs ───────────────────────


class TestRawLogUriIsolation:
    def test_manifest_uri_differs_by_raw_log_id(self, tmp_path) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)
        uri_a = store.raw_log_manifest_uri(version_root, "raw-log-A")
        uri_b = store.raw_log_manifest_uri(version_root, "raw-log-B")
        assert uri_a != uri_b
        assert "raw-log-A" in uri_a
        assert "raw-log-B" in uri_b

    def test_frame_index_uri_differs_by_raw_log_id(self, tmp_path) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)
        uri_a = store.raw_frame_index_uri(version_root, "raw-log-A")
        uri_b = store.raw_frame_index_uri(version_root, "raw-log-B")
        assert uri_a != uri_b
        assert "raw-log-A" in uri_a
        assert "raw-log-B" in uri_b

    def test_scene_segments_uri_differs_by_raw_log_id(self, tmp_path) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)
        uri_a = store.scene_segments_uri(version_root, "raw-log-A")
        uri_b = store.scene_segments_uri(version_root, "raw-log-B")
        assert uri_a != uri_b
        assert "raw-log-A" in uri_a
        assert "raw-log-B" in uri_b

    def test_same_raw_log_id_is_stable(self, tmp_path) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)
        assert store.raw_log_manifest_uri(
            version_root, "raw-log-A"
        ) == store.raw_log_manifest_uri(version_root, "raw-log-A")

    def test_all_three_kinds_isolated_under_same_raw_log_id(self, tmp_path) -> None:
        """Different artifact kinds for the same raw_log_id must not collide
        with each other either."""
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)
        uris = {
            store.raw_log_manifest_uri(version_root, "raw-log-A"),
            store.raw_frame_index_uri(version_root, "raw-log-A"),
            store.scene_segments_uri(version_root, "raw-log-A"),
        }
        assert len(uris) == 3


# ── real write/read isolation: raw log B must not overwrite raw log A ────────


class TestMultiRawLogWriteIsolation:
    """Acceptance test for F-01: two raw logs built into the same
    DatasetVersion must coexist, and writing the second must not disturb
    the first's persisted artifacts."""

    @pytest.mark.asyncio
    async def test_second_raw_log_does_not_overwrite_first_manifest(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)

        uri_a = store.raw_log_manifest_uri(version_root, "raw-log-A")
        await store.save_raw_log_manifest(uri=uri_a, manifest=_manifest("raw-log-A"))

        uri_b = store.raw_log_manifest_uri(version_root, "raw-log-B")
        await store.save_raw_log_manifest(uri=uri_b, manifest=_manifest("raw-log-B"))

        assert uri_a != uri_b
        persisted_a = await store.artifact_store.read_json(uri_a)
        persisted_b = await store.artifact_store.read_json(uri_b)
        assert persisted_a["rawLogId"] == "raw-log-A"
        assert persisted_a["channels"] == ["CHANNEL_raw-log-A"]
        assert persisted_b["rawLogId"] == "raw-log-B"
        assert persisted_b["channels"] == ["CHANNEL_raw-log-B"]

    @pytest.mark.asyncio
    async def test_second_raw_log_does_not_overwrite_first_frame_index(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)

        uri_a = store.raw_frame_index_uri(version_root, "raw-log-A")
        await store.save_raw_frame_index(
            uri=uri_a, frame_index=_frame_index("raw-log-A")
        )

        uri_b = store.raw_frame_index_uri(version_root, "raw-log-B")
        await store.save_raw_frame_index(
            uri=uri_b, frame_index=_frame_index("raw-log-B")
        )

        assert uri_a != uri_b
        persisted_a = await store.artifact_store.read_json(uri_a)
        persisted_b = await store.artifact_store.read_json(uri_b)
        assert persisted_a["rawLogId"] == "raw-log-A"
        assert persisted_b["rawLogId"] == "raw-log-B"

    @pytest.mark.asyncio
    async def test_second_raw_log_does_not_overwrite_first_scene_segments(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)

        uri_a = store.scene_segments_uri(version_root, "raw-log-A")
        await store.save_scene_segment_index(
            uri=uri_a, segment_index=_segment_index("raw-log-A")
        )

        uri_b = store.scene_segments_uri(version_root, "raw-log-B")
        await store.save_scene_segment_index(
            uri=uri_b, segment_index=_segment_index("raw-log-B")
        )

        assert uri_a != uri_b
        persisted_a = await store.artifact_store.read_json(uri_a)
        persisted_b = await store.artifact_store.read_json(uri_b)
        assert persisted_a["rawLogId"] == "raw-log-A"
        assert persisted_b["rawLogId"] == "raw-log-B"

    @pytest.mark.asyncio
    async def test_rewriting_raw_log_a_after_b_leaves_a_byte_identical(
        self, tmp_path
    ) -> None:
        """Full acceptance sequence: write A, write B, then re-read A again
        and confirm nothing about A's file changed as a side effect of B's
        write (same directory tree, sibling raw_log_id namespaces)."""
        store = _store(tmp_path)
        version_root = _version_root(tmp_path)

        uri_a = store.raw_log_manifest_uri(version_root, "raw-log-A")
        await store.save_raw_log_manifest(uri=uri_a, manifest=_manifest("raw-log-A"))
        before = await store.artifact_store.read_json(uri_a)

        uri_b = store.raw_log_manifest_uri(version_root, "raw-log-B")
        await store.save_raw_log_manifest(uri=uri_b, manifest=_manifest("raw-log-B"))

        after = await store.artifact_store.read_json(uri_a)
        assert after == before
