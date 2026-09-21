from __future__ import annotations

from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_SCHEMA_VERSION,
    LearningDataExportConfig,
    learning_data_export_id,
)


def test_export_id_is_deterministic_for_same_inputs() -> None:
    id1 = learning_data_export_id(
        aligned_checksums=["a" * 64, "b" * 64],
        export_config=LearningDataExportConfig(tables=["learning_steps"]),
    )
    id2 = learning_data_export_id(
        aligned_checksums=["a" * 64, "b" * 64],
        export_config=LearningDataExportConfig(tables=["learning_steps"]),
    )
    assert id1 == id2


def test_export_id_is_order_independent_over_checksums() -> None:
    id1 = learning_data_export_id(
        aligned_checksums=["a" * 64, "b" * 64],
        export_config=LearningDataExportConfig(),
    )
    id2 = learning_data_export_id(
        aligned_checksums=["b" * 64, "a" * 64],
        export_config=LearningDataExportConfig(),
    )
    assert id1 == id2


def test_export_id_changes_with_different_checksums() -> None:
    id1 = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(),
    )
    id2 = learning_data_export_id(
        aligned_checksums=["c" * 64],
        export_config=LearningDataExportConfig(),
    )
    assert id1 != id2


def test_export_id_changes_with_different_export_config() -> None:
    id1 = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(tables=["learning_steps"]),
    )
    id2 = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(tables=["learning_signals"]),
    )
    assert id1 != id2


def test_export_id_changes_with_schema_version() -> None:
    id1 = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(),
        schema_version="v1",
    )
    id2 = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(),
        schema_version="v2",
    )
    assert id1 != id2


def test_export_id_defaults_to_current_schema_version() -> None:
    id_default = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(),
    )
    id_explicit = learning_data_export_id(
        aligned_checksums=["a" * 64],
        export_config=LearningDataExportConfig(),
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
    )
    assert id_default == id_explicit


def test_export_id_ignores_duplicate_adjacent_calls_with_same_set_different_list_identity() -> (
    None
):
    """Two distinct list objects with the same contents/order produce the
    same id -- confirms no object-identity leakage into the hash."""
    checksums_a = list(["a" * 64, "b" * 64])
    checksums_b = ["a" * 64, "b" * 64]
    assert learning_data_export_id(
        aligned_checksums=checksums_a, export_config=LearningDataExportConfig()
    ) == learning_data_export_id(
        aligned_checksums=checksums_b, export_config=LearningDataExportConfig()
    )
