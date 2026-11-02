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
    ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION,
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    UnsupportedAlignedArtifactSchemaError,
    align_episode,
    alignment_config_hash,
    alignment_key,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_core.episodes.testing import DEFAULT_CLOCK, episode_manifest, state
from sceneops_episodes.artifacts import (
    EpisodeArtifactStore,
    EpisodeManifestIntegrityError,
    EpisodeManifestWriteConflictError,
)


def _store(tmp_path) -> EpisodeArtifactStore:
    return EpisodeArtifactStore(
        artifact_store=LocalArtifactStore(root_uri=str(tmp_path)),
        canonical_root_uri=str(tmp_path / "canonical"),
        aligned_root_uri=str(tmp_path / "aligned_episodes"),
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
            episode_id="ep-1", manifest=manifest
        )
        data = manifest.to_canonical_bytes()
        assert published.checksum == "sha256:" + hashlib.sha256(data).hexdigest()
        # Unit-scoped: the key names the revision and no DatasetVersion.
        assert published.uri == str(
            tmp_path
            / "canonical"
            / f"episodes/ep-1/manifest-{hashlib.sha256(data).hexdigest()}.json"
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
        args = dict(episode_id="ep-1")
        a = await store.publish_canonical_manifest(manifest=_manifest(), **args)
        b = await store.publish_canonical_manifest(manifest=_manifest(), **args)
        assert a == b

    @pytest.mark.asyncio
    async def test_revisions_coexist_and_keys_are_write_once(self, tmp_path) -> None:
        store = _store(tmp_path)
        args = dict(episode_id="ep-1")
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
    def test_names_the_episode_revision_and_recipe_and_no_dataset_version(
        self, tmp_path
    ) -> None:
        uri = _store(tmp_path).aligned_episode_uri(
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
        )
        assert uri == str(
            tmp_path / "aligned_episodes" / "ep-1" / ("a" * 16) / f"{'k' * 16}.json"
        )

    def test_changes_with_the_artifact_schema_version(self, tmp_path) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        uris = {
            _store(tmp_path).aligned_episode_uri(
                episode_id="ep-1",
                source_manifest_sha256="a" * 64,
                alignment_key=alignment_key(
                    config, "v3", DEFAULT_CLOCK, artifact_schema_version=schema
                ),
            )
            for schema in ("v2", "v3")
        }
        assert len(uris) == 2

    def test_changes_when_source_hash_changes(self, tmp_path) -> None:
        store = _store(tmp_path)
        uri_a = store.aligned_episode_uri(
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
        )
        uri_b = store.aligned_episode_uri(
            episode_id="ep-1",
            source_manifest_sha256="b" * 64,
            alignment_key="k" * 64,
        )
        assert uri_a != uri_b

    def test_changes_when_alignment_key_changes(self, tmp_path) -> None:
        store = _store(tmp_path)
        uri_a = store.aligned_episode_uri(
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
        )
        uri_b = store.aligned_episode_uri(
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="j" * 64,
        )
        assert uri_a != uri_b

    def test_identical_for_semantically_identical_inputs(self, tmp_path) -> None:
        store = _store(tmp_path)
        kwargs = dict(
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
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key=key,
            artifact=artifact,
        )

        on_disk = await store.artifact_store.read_bytes(result.uri)
        assert result.checksum == f"sha256:{hashlib.sha256(on_disk).hexdigest()}"

        round_tripped = load_aligned_episode_artifact(on_disk)
        assert round_tripped.source_revision.source_artifact_id == "art-1"
        assert round_tripped.aligned_episode.episode_id == "ep-1"
        assert round_tripped.schema_version == "v2"
        assert round_tripped.aligned_episode.clock_instance == manifest.clock_instance(
            DEFAULT_CLOCK
        )
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


def _legacy_v1_key(config: TemporalAlignmentConfig, semantics: str, clock: str) -> str:
    """The key artifacts of schema v1 were written at, spelled out: the recipe
    hash had no schema input then."""
    payload = json.dumps(
        {
            "alignment_config_hash": alignment_config_hash(config),
            "alignment_semantics_version": semantics,
            "source_clock": clock,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TestSchemaUpgradeOnStorage:
    """A real store holding a real v1 object at its v1 key: the upgraded writer
    lands beside it, and what P5.0 found (new bytes at the unchanged key) is kept
    as the negative control."""

    CONFIG = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
    SOURCE_SHA = "a" * 64

    def _artifact(self) -> AlignedEpisodeArtifact:
        return AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id="ep-1",
                episode_manifest_uri="file:///source.json",
                source_artifact_id="art-1",
                source_manifest_sha256=self.SOURCE_SHA,
            ),
            aligned_episode=align_episode(
                _manifest(),
                self.CONFIG,
                TemporalSourceContext(source_clock=DEFAULT_CLOCK),
                episode_id="ep-1",
            ),
        )

    def _location(self, key: str) -> dict:
        return dict(
            episode_id="ep-1",
            source_manifest_sha256=self.SOURCE_SHA,
            alignment_key=key,
        )

    async def _seed_v1(
        self, store: EpisodeArtifactStore, key: str
    ) -> tuple[str, bytes]:
        document = self._artifact().to_artifact_dict()
        document["schemaVersion"] = "v1"
        del document["alignedEpisode"]["clockOrigin"]
        del document["alignedEpisode"]["clockScope"]
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        uri = store.aligned_episode_uri(**self._location(key))
        await store.artifact_store.write_bytes(uri, data)
        return uri, data

    def _keys(self, artifact: AlignedEpisodeArtifact) -> tuple[str, str]:
        aligned = artifact.aligned_episode
        return (
            _legacy_v1_key(
                self.CONFIG, aligned.alignment_semantics_version, aligned.source_clock
            ),
            alignment_key(
                self.CONFIG, aligned.alignment_semantics_version, aligned.source_clock
            ),
        )

    @pytest.mark.asyncio
    async def test_v2_lands_beside_v1_and_leaves_its_object_intact(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        artifact = self._artifact()
        v1_key, v2_key = self._keys(artifact)
        assert v1_key != v2_key
        v1_uri, v1_bytes = await self._seed_v1(store, v1_key)

        written = await store.write_aligned_episode(
            artifact=artifact, **self._location(v2_key)
        )

        assert written.uri != v1_uri
        assert await store.artifact_store.read_bytes(v1_uri) == v1_bytes
        reread = load_aligned_episode_artifact(
            await store.artifact_store.read_bytes(written.uri)
        )
        assert reread.schema_version == ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION
        assert reread == artifact

    @pytest.mark.asyncio
    async def test_a_repeated_v2_write_converges_on_one_object(self, tmp_path) -> None:
        store = _store(tmp_path)
        location = self._location(self._keys(self._artifact())[1])
        one = await store.write_aligned_episode(artifact=self._artifact(), **location)
        two = await store.write_aligned_episode(artifact=self._artifact(), **location)
        assert (one.uri, one.checksum, one.size_bytes) == (
            two.uri,
            two.checksum,
            two.size_bytes,
        )

    @pytest.mark.asyncio
    async def test_control_new_bytes_at_the_unchanged_v1_key_conflict(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        artifact = self._artifact()
        v1_key, _ = self._keys(artifact)
        v1_uri, v1_bytes = await self._seed_v1(store, v1_key)

        with pytest.raises(EpisodeManifestWriteConflictError):
            await store.write_aligned_episode(
                artifact=artifact, **self._location(v1_key)
            )
        assert await store.artifact_store.read_bytes(v1_uri) == v1_bytes

    @pytest.mark.asyncio
    async def test_the_stored_v1_object_is_refused_by_the_reader(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        v1_key, _ = self._keys(self._artifact())
        v1_uri, _ = await self._seed_v1(store, v1_key)
        with pytest.raises(UnsupportedAlignedArtifactSchemaError, match="'v1'"):
            load_aligned_episode_artifact(
                await store.read_aligned_episode_bytes(v1_uri)
            )


class TestAlignedEpisodeReportStorage:
    """Reports are named by their own content: a re-run of a changed validator
    or profiler is a new revision beside the old one, never a replacement."""

    @staticmethod
    def _report(**fields):
        from sceneops_core.common.schemas import SceneOpsBaseModel

        class _Report(SceneOpsBaseModel):
            valid: bool = True
            issue_count: int = 0

        return _Report(**fields)

    @staticmethod
    def _kwargs():
        return dict(
            episode_id="ep-1",
            source_manifest_sha256="a" * 64,
            alignment_key="k" * 64,
            report_kind="validation",
        )

    @pytest.mark.asyncio
    async def test_a_report_pins_its_bytes_and_a_retry_converges(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        one = await store.write_aligned_episode_report(
            report=self._report(), **self._kwargs()
        )
        again = await store.write_aligned_episode_report(
            report=self._report(), **self._kwargs()
        )

        data = await store.artifact_store.read_bytes(one.uri)
        assert one.checksum == "sha256:" + hashlib.sha256(data).hexdigest()
        assert one.checksum.removeprefix("sha256:") in one.uri
        assert (again.uri, again.checksum) == (one.uri, one.checksum)

    @pytest.mark.asyncio
    async def test_a_changed_report_is_a_new_revision_and_keeps_the_old_bytes(
        self, tmp_path
    ) -> None:
        store = _store(tmp_path)
        one = await store.write_aligned_episode_report(
            report=self._report(), **self._kwargs()
        )
        old_bytes = await store.artifact_store.read_bytes(one.uri)

        two = await store.write_aligned_episode_report(
            report=self._report(valid=False, issue_count=2), **self._kwargs()
        )

        assert two.uri != one.uri
        assert await store.artifact_store.read_bytes(one.uri) == old_bytes
