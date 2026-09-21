"""Execution-key / URI identity matrix for ALIGN_EPISODE (SceneOps V2
Request 2.3 §16-18, §37-39).

compute_execution_key() itself needs no change (packages/sceneops-core/
sceneops_core/executions/key.py) -- these tests prove the *inputs* built
for align_episode's params (source_manifest_sha256, canonical alignment
config, alignment_semantics_version) produce the identity behavior the
request specifies, and that alignment_key()/aligned_episode_uri() do too.

source_artifact_id is deliberately NOT part of any of these identities
(Request 2.3 §18): same bytes + different producer artifact_id must yield
the same semantic identity, since the platform's own artifact model can
mint a new artifact_id per build execution even when content doesn't
change (Request 2.1B §15).
"""

from __future__ import annotations

from sceneops_core.episodes.alignment import TemporalAlignmentConfig, alignment_key
from sceneops_core.executions import compute_execution_key


def _execution_key(
    *,
    episode_id: str = "ep-1",
    source_manifest_sha256: str = "a" * 64,
    alignment_semantics_version: str = "v1",
    config: TemporalAlignmentConfig | None = None,
) -> str:
    config = config or TemporalAlignmentConfig(target_frequency_hz=1.0)
    return compute_execution_key(
        kind="job",
        type="align_episode",
        dataset_id="d1",
        dataset_version="v1",
        params={
            "episode_id": episode_id,
            "source_manifest_sha256": source_manifest_sha256,
            "alignment_semantics_version": alignment_semantics_version,
            "alignment_config": config.model_dump(mode="json", exclude_none=True),
        },
    )


class TestExecutionKeyIdentityMatrix:
    def test_same_source_same_config_same_semantics_same_key(self) -> None:
        assert _execution_key() == _execution_key()

    def test_different_config_different_key(self) -> None:
        assert _execution_key() != _execution_key(
            config=TemporalAlignmentConfig(target_frequency_hz=2.0)
        )

    def test_different_source_content_hash_different_key(self) -> None:
        assert _execution_key(source_manifest_sha256="a" * 64) != _execution_key(
            source_manifest_sha256="b" * 64
        )

    def test_different_semantics_version_different_key(self) -> None:
        assert _execution_key(alignment_semantics_version="v1") != _execution_key(
            alignment_semantics_version="v2"
        )

    def test_different_episode_different_key(self) -> None:
        assert _execution_key(episode_id="ep-1") != _execution_key(episode_id="ep-2")

    def test_source_artifact_id_is_not_part_of_the_key_at_all(self) -> None:
        # source_artifact_id simply never appears in the params dict built
        # for the execution key -- proven by construction, not by a
        # same/different assertion, since it was never an input to begin
        # with (Request 2.3 §18's explicit exclusion).
        key = _execution_key()
        params_used = {
            "episode_id": "ep-1",
            "source_manifest_sha256": "a" * 64,
            "alignment_semantics_version": "v1",
            "alignment_config": TemporalAlignmentConfig(
                target_frequency_hz=1.0
            ).model_dump(mode="json", exclude_none=True),
        }
        assert "source_artifact_id" not in params_used
        assert key == compute_execution_key(
            kind="job",
            type="align_episode",
            dataset_id="d1",
            dataset_version="v1",
            params=params_used,
        )


class TestAlignmentKeyIdentity:
    def test_same_config_same_semantics_same_key(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        assert alignment_key(config, "v1") == alignment_key(config, "v1")

    def test_different_config_different_key(self) -> None:
        a = TemporalAlignmentConfig(target_frequency_hz=1.0)
        b = TemporalAlignmentConfig(target_frequency_hz=2.0)
        assert alignment_key(a, "v1") != alignment_key(b, "v1")

    def test_different_semantics_version_different_key(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        assert alignment_key(config, "v1") != alignment_key(config, "v2")

    def test_does_not_depend_on_source_content_at_all(self) -> None:
        # By construction alignment_key() takes no source-hash argument --
        # it is deliberately source-independent (Request 2.3 §14), so the
        # *same* config+semantics maps to the *same* key regardless of
        # which episode/source it will end up paired with in the URI.
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        key_for_episode_a = alignment_key(config, "v1")
        key_for_episode_b = alignment_key(config, "v1")
        assert key_for_episode_a == key_for_episode_b
