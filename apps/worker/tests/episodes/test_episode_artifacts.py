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
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
    alignment_key,
)
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_core.episodes.testing import DEFAULT_CLOCK, episode_manifest, state
from sceneops_worker.episodes.artifacts import (
    EpisodeArtifactStore,
    EpisodeManifestIntegrityError,
    EpisodeManifestWriteConflictError,
)


def _store(tmp_path) -> EpisodeArtifactStore:
    return EpisodeArtifactStore(
        artifact_store=LocalArtifactStore(root_uri=str(tmp_path)),
        dataset_root_uri=str(tmp_path / "datasets"),
        payload_root_uri=str(tmp_path / "payloads"),
    )


def _manifest(x: float = 0.0) -> EpisodeManifest:
    return episode_manifest([state("/odom", 0, x=x)], window=(0, 1))


class TestCanonicalEpisodeManifestStorage:
    @pytest.mark.asyncio
    async def test_published_bytes_are_canonical_and_checksum_qualified(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        manifest = _manifest()
        published = await store.publish_canonical_manifest(
            dataset_id="d1", dataset_version="v1", episode_id="ep-1", manifest=manifest
        )
        data = manifest.to_canonical_bytes()
        assert published.checksum == "sha256:" + hashlib.sha256(data).hexdigest()
        assert published.uri.endswith(
            f"episodes/ep-1/manifest-{hashlib.sha256(data).hexdigest()}.json"
        )
        assert (
            await store.read_pinned_manifest(
                uri=published.uri, checksum=published.checksum, size_bytes=len(data)
            )
            == manifest
        )

    @pytest.mark.asyncio
    async def test_republishing_identical_bytes_converges(self, tmp_path) -> None:
        store = _store(tmp_path)
        args = dict(dataset_id="d1", dataset_version="v1", episode_id="ep-1")
        a = await store.publish_canonical_manifest(manifest=_manifest(), **args)
        b = await store.publish_canonical_manifest(manifest=_manifest(), **args)
        assert a == b

    @pytest.mark.asyncio
    async def test_revisions_coexist_and_keys_are_write_once(self, tmp_path) -> None:
        store = _store(tmp_path)
        args = dict(dataset_id="d1", dataset_version="v1", episode_id="ep-1")
        a = await store.publish_canonical_manifest(manifest=_manifest(0.0), **args)
        b = await store.publish_canonical_manifest(manifest=_manifest(1.0), **args)
        assert a.uri != b.uri
        await store.artifact_store.write_bytes(a.uri, b"tampered")
        with pytest.raises(EpisodeManifestWriteConflictError):
            await store.publish_canonical_manifest(manifest=_manifest(0.0), **args)
        with pytest.raises(EpisodeManifestIntegrityError):
            await store.read_pinned_manifest(uri=a.uri, checksum=a.checksum)

    @pytest.mark.asyncio
    async def test_missing_bytes_are_an_integrity_error(self, tmp_path) -> None:
        with pytest.raises(EpisodeManifestIntegrityError):
            await _store(tmp_path).read_pinned_manifest(
                uri=str(tmp_path / "nope.json"), checksum="sha256:" + "0" * 64
            )


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
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        ctx = TemporalSourceContext(source_clock=DEFAULT_CLOCK)
        aligned = align_episode(manifest, config, ctx, episode_id="ep-1")

        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id="ep-1",
                episode_manifest_uri="file:///source.json",
                source_artifact_id="art-1",
                source_manifest_sha256="a" * 64,
            ),
            aligned_episode=aligned,
        )
        key = alignment_key(
            config, aligned.alignment_semantics_version, aligned.source_clock
        )

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


class TestAlignedEpisodeIsWriteOnce:
    @pytest.mark.asyncio
    async def test_a_retry_with_identical_content_is_a_no_op_and_a_change_conflicts(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        ctx = TemporalSourceContext(source_clock=DEFAULT_CLOCK)

        def artifact(x: float) -> AlignedEpisodeArtifact:
            return AlignedEpisodeArtifact(
                source_revision=EpisodeSourceRevision(
                    episode_id="ep-1",
                    episode_manifest_uri="file:///source.json",
                    source_artifact_id="art-1",
                    source_manifest_sha256="a" * 64,
                ),
                aligned_episode=align_episode(
                    _manifest(x), config, ctx, episode_id="ep-1"
                ),
            )

        first = artifact(0.0)
        key = alignment_key(
            config, first.aligned_episode.alignment_semantics_version, DEFAULT_CLOCK
        )
        kwargs = dict(
            dataset_id="d1",
            dataset_version="v1",
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key=key,
        )
        one = await store.write_aligned_episode(artifact=first, **kwargs)
        again = await store.write_aligned_episode(artifact=first, **kwargs)
        assert (again.uri, again.checksum) == (one.uri, one.checksum)

        with pytest.raises(EpisodeManifestWriteConflictError, match="write-once"):
            await store.write_aligned_episode(artifact=artifact(1.0), **kwargs)
        # The original bytes survive the refused write.
        assert (await store.artifact_store.read_bytes(one.uri)) is not None
        assert (
            "sha256:"
            + hashlib.sha256(await store.artifact_store.read_bytes(one.uri)).hexdigest()
            == one.checksum
        )
