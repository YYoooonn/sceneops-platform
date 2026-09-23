"""Tests for ExternalDatasetRef (SceneOps V2 Request 3.2B): the shared
reference type for datasets outside SceneOps' canonical domain model --
import sources (nuScenes) and export targets (LeRobot/RLDS) alike.
"""

from __future__ import annotations

from sceneops_core.datasets import ExternalDatasetRef


def test_minimal_ref_only_requires_format_version_and_uri():
    ref = ExternalDatasetRef(
        format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
    )
    assert ref.format == "nuscenes"
    assert ref.format_version == "v1.0-mini"
    assert ref.uri == "/data/raw/nuscenes"
    assert ref.external_name is None
    assert ref.external_revision is None
    assert ref.checksum is None


def test_same_shape_covers_import_and_export_directions():
    """Direction belongs to the operation, not the ref -- one shape covers
    both a nuScenes import source and a LeRobot export target."""
    source = ExternalDatasetRef(
        format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
    )
    export_target = ExternalDatasetRef(
        format="lerobot",
        format_version="2.1",
        uri="s3://sceneops/exports/lerobot/test-e2e-core",
    )
    assert type(source) is type(export_target)


def test_format_is_not_constrained_to_a_closed_enum():
    # Open-ended on purpose -- new external formats (rlds, ...) never
    # require a schema change here.
    ref = ExternalDatasetRef(
        format="rlds", format_version="1.0.0", uri="gs://bucket/ds"
    )
    assert ref.format == "rlds"


def test_optional_identity_fields_round_trip():
    ref = ExternalDatasetRef(
        format="nuscenes",
        format_version="v1.0-mini",
        uri="/data/raw/nuscenes",
        external_name="nuScenes mini",
        external_revision="v1.0",
        checksum="sha256:" + "a" * 64,
    )
    assert ref.external_name == "nuScenes mini"
    assert ref.external_revision == "v1.0"
    assert ref.checksum == "sha256:" + "a" * 64
