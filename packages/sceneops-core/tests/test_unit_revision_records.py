"""Unit, revision and member records (ADR-015 §3, §6.4, §7).

A unit record is its id and source binding; a revision record is one
manifest of a unit, pinned by its deterministic manifest artifact id; a
DatasetVersion member pins one revision. None of them holds membership,
status or a "current" pointer, and each rejects an identity that its own
fields contradict.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneops_core.datasets.schemas import DatasetVersionMember
from sceneops_core.episodes.schemas import (
    EpisodeRecord,
    EpisodeRevisionRecord,
    episode_id_for,
    project_episode_revision,
)
from sceneops_core.episodes.testing import action, episode_manifest, state
from sceneops_core.provenance import RevisionPinError, manifest_artifact_id_for
from sceneops_core.scenes.schemas import (
    SceneRecord,
    SceneRevisionRecord,
    project_scene_revision,
    scene_id_for,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source


def _scene_revision(**source) -> SceneRevisionRecord:
    manifest = build_scene_manifest(source=recording_source(**source))
    return project_scene_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id_for(manifest.lineage.source),
            manifest_checksum=manifest.checksum(),
        ),
        manifest_checksum=manifest.checksum(),
    )


def _episode_revision() -> EpisodeRevisionRecord:
    manifest = episode_manifest(
        [state("/odom", 0, x=1.0), action("/control", 5, u=0.1)]
    )
    return project_episode_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=episode_id_for(manifest.lineage.source),
            manifest_checksum=manifest.checksum(),
        ),
        manifest_checksum=manifest.checksum(),
    )


def test_records_hold_no_membership_status_or_current_pointer():
    forbidden = {"dataset_id", "dataset_version", "status", "updated_at", "current"}
    for model in (
        SceneRecord,
        SceneRevisionRecord,
        EpisodeRecord,
        EpisodeRevisionRecord,
    ):
        assert not forbidden & set(model.model_fields)
    assert not {"manifest_artifact_id", "manifest_checksum"} & set(
        SceneRecord.model_fields
    )


def test_a_revision_names_its_unit():
    revision = _scene_revision(robot_run_id="run-7", unit_key="seg/1")
    unit = revision.unit()
    assert unit == SceneRecord(
        scene_id=revision.scene_id,
        source_kind="recording",
        robot_run_id="run-7",
        unit_key="seg/1",
    )
    assert unit.scene_id == scene_id_for(
        recording_source(robot_run_id="run-7", unit_key="seg/1")
    )
    episode = _episode_revision()
    assert episode.unit().episode_id == episode.episode_id


@pytest.mark.parametrize(
    "field, value",
    [("robot_run_id", "run-other"), ("unit_key", "seg/2")],
)
def test_a_unit_record_rejects_a_binding_its_id_was_not_derived_from(field, value):
    unit = _scene_revision(unit_key="seg/1").unit()
    with pytest.raises(ValidationError, match="is not the unit of"):
        SceneRecord.model_validate({**unit.model_dump(), field: value})


def test_a_revision_record_rejects_an_artifact_id_other_than_its_pins():
    revision = _scene_revision()
    other = _scene_revision(unit_key="other")
    with pytest.raises(ValidationError, match="is not the revision"):
        SceneRevisionRecord.model_validate(
            {
                **revision.model_dump(),
                "manifest_artifact_id": other.manifest_artifact_id,
            }
        )
    episode = _episode_revision()
    with pytest.raises(ValidationError, match="is not the revision"):
        EpisodeRevisionRecord.model_validate(
            {**episode.model_dump(), "manifest_checksum": "sha256:" + "9" * 64}
        )


def test_projection_excludes_only_the_registration_time():
    revision = _scene_revision()
    assert set(revision.projection()) == set(SceneRevisionRecord.model_fields) - {
        "registered_at"
    }


def test_a_member_pins_one_revision_of_its_domain():
    revision = _scene_revision()
    member = DatasetVersionMember(
        domain="scene",
        unit_id=revision.scene_id,
        manifest_artifact_id=revision.manifest_artifact_id,
        manifest_checksum=revision.manifest_checksum,
    )
    assert member.unit_id == revision.scene_id

    with pytest.raises(ValidationError, match="is not a episode unit"):
        DatasetVersionMember(**{**member.model_dump(), "domain": "episode"})
    with pytest.raises(
        (ValidationError, RevisionPinError), match="is not the revision"
    ):
        DatasetVersionMember(
            **{**member.model_dump(), "manifest_checksum": "sha256:" + "0" * 64}
        )
    assert "dataset_id" not in DatasetVersionMember.model_fields
