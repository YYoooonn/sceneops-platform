"""Tests for EpisodeArtifactStore's checksum hardening and aligned-episode
URI/write path (SceneOps V2 Request 2.3 §3/§14/§34).

Uses a real LocalArtifactStore against tmp_path -- not a mock -- so the
checksum assertions prove something about actual bytes on disk, not just
about what a mock was told to return.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sceneops_storage import LocalArtifactStore

from sceneops_core.episodes.alignment import (
    MCAP_LOG_TIME_CLOCK,
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
    alignment_key,
)
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_worker.episodes.artifacts import EpisodeArtifactStore


def _store(tmp_path) -> EpisodeArtifactStore:
    return EpisodeArtifactStore(
        artifact_store=LocalArtifactStore(root_uri=str(tmp_path)),
        dataset_root_uri=str(tmp_path / "datasets"),
    )


def _manifest(episode_id: str = "ep-1") -> EpisodeManifest:
    return EpisodeManifest(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        start_timestamp_us=0,
        end_timestamp_us=0,
        frame_count=0,
        task="park",
    )


class TestEpisodeManifestChecksum:
    @pytest.mark.asyncio
    async def test_checksum_equals_sha256_of_actual_written_bytes(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        manifest = _manifest()

        result = await store.write_episode_manifest(
            dataset_id="d1", dataset_version="v1", episode_id="ep-1", manifest=manifest
        )

        on_disk_bytes = await store.artifact_store.read_bytes(result.uri)
        expected = f"sha256:{hashlib.sha256(on_disk_bytes).hexdigest()}"
        assert result.checksum == expected
        assert result.size_bytes == len(on_disk_bytes)

    @pytest.mark.asyncio
    async def test_written_bytes_are_valid_json_readable_via_read_json(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        manifest = _manifest()
        result = await store.write_episode_manifest(
            dataset_id="d1", dataset_version="v1", episode_id="ep-1", manifest=manifest
        )
        loaded = await store.load_episode_manifest(result.uri)
        assert loaded is not None
        assert loaded.episode_id == "ep-1"

    @pytest.mark.asyncio
    async def test_different_content_produces_different_checksum(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        result_a = await store.write_episode_manifest(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            manifest=_manifest("ep-1"),
        )
        result_b = await store.write_episode_manifest(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-2",
            manifest=_manifest("ep-2"),
        )
        assert result_a.checksum != result_b.checksum

    @pytest.mark.asyncio
    async def test_read_episode_manifest_bytes_matches_what_was_written(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        result = await store.write_episode_manifest(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            manifest=_manifest(),
        )
        raw = await store.read_episode_manifest_bytes(result.uri)
        assert raw is not None
        assert f"sha256:{hashlib.sha256(raw).hexdigest()}" == result.checksum
        # And it's real JSON, not an opaque blob.
        assert json.loads(raw)["episodeId"] == "ep-1"

    @pytest.mark.asyncio
    async def test_read_episode_manifest_bytes_missing_returns_none(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        assert await store.read_episode_manifest_bytes("file:///nope.json") is None


class TestAlignedEpisodeUri:
    def test_changes_when_source_hash_changes(self, tmp_path) -> None:
        store = _store(tmp_path)
        uri_a = store.aligned_episode_uri(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
        )
        uri_b = store.aligned_episode_uri(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="b" * 64,
            alignment_key="k" * 64,
        )
        assert uri_a != uri_b

    def test_changes_when_alignment_key_changes(self, tmp_path) -> None:
        store = _store(tmp_path)
        uri_a = store.aligned_episode_uri(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
        )
        uri_b = store.aligned_episode_uri(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="j" * 64,
        )
        assert uri_a != uri_b

    def test_identical_for_semantically_identical_inputs(self, tmp_path) -> None:
        store = _store(tmp_path)
        kwargs = dict(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
        )
        assert store.aligned_episode_uri(**kwargs) == store.aligned_episode_uri(
            **kwargs
        )


class TestWriteAlignedEpisode:
    @pytest.mark.asyncio
    async def test_round_trips_the_envelope_and_checksums_actual_bytes(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        manifest = _manifest()
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        ctx = TemporalSourceContext(source_clock=MCAP_LOG_TIME_CLOCK)
        aligned = align_episode(manifest, config, ctx)

        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id="ep-1",
                episode_manifest_uri="file:///source.json",
                source_artifact_id="art-1",
                source_manifest_sha256="a" * 64,
            ),
            aligned_episode=aligned,
        )
        key = alignment_key(config, aligned.alignment_semantics_version)

        result = await store.write_aligned_episode(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key=key,
            artifact=artifact,
        )

        on_disk = await store.artifact_store.read_bytes(result.uri)
        assert result.checksum == f"sha256:{hashlib.sha256(on_disk).hexdigest()}"

        round_tripped = AlignedEpisodeArtifact.model_validate(json.loads(on_disk))
        assert round_tripped.source_revision.source_artifact_id == "art-1"
        assert round_tripped.aligned_episode.episode_id == "ep-1"
        assert round_tripped.schema_version == "v1"
        # Operational fields never appear in the envelope.
        assert "job_id" not in json.loads(on_disk)
        assert "pipeline_run_id" not in json.loads(on_disk)
