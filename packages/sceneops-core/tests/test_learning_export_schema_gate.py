"""A learning export manifest must state the schema this code reads, on every way
of parsing one, and the shard reuse of an incremental export relies on it. The
tables behind a manifest are checked in sceneops-analytics."""

from __future__ import annotations

import json

import pytest

from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_SCHEMA_VERSION,
    AlignedArtifactRevision,
    IncrementalExportUnsupportedBaseError,
    LearningDataExportConfig,
    LearningDataExportManifest,
    LearningDataShardIndex,
    ShardPolicy,
    UnsupportedLearningDataSchemaError,
    learning_data_export_id,
    plan_incremental_export,
)

_REVISION = AlignedArtifactRevision(
    episode_id="ep-1", aligned_artifact_id="art-1", aligned_artifact_checksum="c1"
)


def _manifest() -> LearningDataExportManifest:
    return LearningDataExportManifest(
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
        export_id="e1",
        dataset_id="d",
        dataset_version="v",
        inputs=[_REVISION],
        shard_index=LearningDataShardIndex(
            shard_policy=ShardPolicy(max_episodes_per_shard=1, max_rows_per_shard=1)
        ),
    )


def _document(**overrides) -> dict:
    document = _manifest().to_artifact_dict()
    document.update(overrides)
    return document


def test_a_manifest_states_the_supported_version_when_stored() -> None:
    document = _manifest().to_artifact_dict()
    assert document["schemaVersion"] == LEARNING_DATA_SCHEMA_VERSION == "v2"
    assert LearningDataExportManifest.model_validate(document) == _manifest()


@pytest.mark.parametrize("version", ["v1", "v3", "v999", "V2", "", None, 2, ["v2"]])
def test_every_other_stated_version_is_refused(version) -> None:
    document = _document(schemaVersion=version)
    with pytest.raises(UnsupportedLearningDataSchemaError):
        LearningDataExportManifest.model_validate(document)
    with pytest.raises(UnsupportedLearningDataSchemaError):
        LearningDataExportManifest.model_validate_json(json.dumps(document))


def test_a_stored_document_stating_no_version_is_not_read_as_the_current_one() -> None:
    document = _document()
    del document["schemaVersion"]
    with pytest.raises(UnsupportedLearningDataSchemaError, match="None"):
        LearningDataExportManifest.model_validate_json(json.dumps(document))
    with pytest.raises(UnsupportedLearningDataSchemaError, match="None"):
        LearningDataExportManifest.model_validate(document)


def test_a_writer_states_the_version_too() -> None:
    with pytest.raises(UnsupportedLearningDataSchemaError):
        LearningDataExportManifest(export_id="e", dataset_id="d", dataset_version="v")
    with pytest.raises(UnsupportedLearningDataSchemaError):
        LearningDataExportManifest(
            schema_version="v1", export_id="e", dataset_id="d", dataset_version="v"
        )


def test_the_snake_case_spelling_is_gated_alike() -> None:
    document = _manifest().model_dump(mode="json")
    assert LearningDataExportManifest.model_validate(document) == _manifest()
    document["schema_version"] = "v1"
    with pytest.raises(UnsupportedLearningDataSchemaError):
        LearningDataExportManifest.model_validate(document)


def test_the_refusal_is_not_folded_into_a_generic_validation_error() -> None:
    """A caller tells an unsupported version from a malformed document."""
    assert not issubclass(UnsupportedLearningDataSchemaError, ValueError)


@pytest.mark.parametrize("version", ["v1", "v999"])
def test_the_planner_refuses_a_base_of_another_schema_before_any_reuse(version) -> None:
    """``model_copy`` is how an object skips validation; the planner still refuses
    to carry its shards into an export of this schema."""
    base = _manifest().model_copy(update={"schema_version": version})
    target = [
        _REVISION,
        AlignedArtifactRevision(
            episode_id="ep-2",
            aligned_artifact_id="art-2",
            aligned_artifact_checksum="c2",
        ),
    ]
    with pytest.raises(IncrementalExportUnsupportedBaseError, match=version):
        plan_incremental_export(base, target)


def test_export_identity_keeps_the_schema_input_it_always_had() -> None:
    """v2 needs no competing identity: the schema version was already hashed, so
    the same inputs get a new export id, hence new object locations."""
    config = LearningDataExportConfig()
    current = learning_data_export_id(aligned_checksums=["a"], export_config=config)
    assert current != learning_data_export_id(
        aligned_checksums=["a"], export_config=config, schema_version="v1"
    )
    assert current == learning_data_export_id(
        aligned_checksums=["a"],
        export_config=config,
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
    )
