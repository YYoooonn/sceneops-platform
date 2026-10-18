"""Job params of the Build -> Register -> Compose -> assess lifecycle
(ADR-015 §6.4, §6.7, §7).

A build and a registration name no DatasetVersion; validation and profiling
take complete revision pins of their own domain, one per unit. Each stale
field is refused rather than ignored, so an old caller fails loudly instead
of believing it scoped the work to a DatasetVersion or let the job choose a
revision.
"""

from __future__ import annotations

import pytest

from sceneops_core.jobs.schemas import (
    BuildRecordingEpisodesJobParams,
    BuildRecordingScenesJobParams,
    ProfileEpisodeJobParams,
    ProfileSceneJobParams,
    ValidateEpisodeJobParams,
    ValidateSceneJobParams,
)
from sceneops_core.provenance import RevisionPin, manifest_artifact_id_for, unit_id_for

_CHECKSUM = "sha256:" + "a" * 64
_DATASET_VERSION_FIELDS = (
    "dataset_id",
    "datasetId",
    "dataset_version",
    "datasetVersion",
)


def _pin(
    domain: str, unit_key: str = "fixed_duration-0123456789abcdef/0"
) -> RevisionPin:
    unit_id = unit_id_for(
        domain=domain, source_kind="recording", source_id="run-001", unit_key=unit_key
    )
    return RevisionPin(
        domain=domain,
        unit_id=unit_id,
        manifest_checksum=_CHECKSUM,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=unit_id, manifest_checksum=_CHECKSUM
        ),
    )


@pytest.mark.parametrize(
    "params", [BuildRecordingScenesJobParams, BuildRecordingEpisodesJobParams]
)
@pytest.mark.parametrize("field", _DATASET_VERSION_FIELDS)
def test_a_build_names_no_dataset_version(params, field):
    assert not {"dataset_id", "dataset_version"} & params.model_fields.keys()
    with pytest.raises(ValueError, match=f"{field} is not accepted"):
        params.model_validate({"robot_run_id": "run-001", field: "x"})


_ASSESSMENTS = [
    (ValidateSceneJobParams, "scene", ("scene_ids", "sceneIds")),
    (ProfileSceneJobParams, "scene", ("scene_ids", "sceneIds")),
    (ValidateEpisodeJobParams, "episode", ("episode_id", "episode_ids", "episodeIds")),
    (ProfileEpisodeJobParams, "episode", ("episode_id", "episode_ids", "episodeIds")),
]


@pytest.mark.parametrize(("params", "domain", "unpinned"), _ASSESSMENTS)
def test_an_assessment_takes_complete_pins_of_its_domain(params, domain, unpinned):
    pin = _pin(domain)
    assert params.model_validate({"revisions": [pin.model_dump()]}).revisions == [pin]

    with pytest.raises(ValueError, match="revisions"):
        params.model_validate({"revisions": []})
    for stale in (*unpinned, *_DATASET_VERSION_FIELDS):
        with pytest.raises(ValueError, match=f"{stale} is not accepted"):
            params.model_validate({"revisions": [pin], stale: "x"})

    other_revision = "sha256:" + "b" * 64
    same_unit = RevisionPin(
        domain=domain,
        unit_id=pin.unit_id,
        manifest_checksum=other_revision,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=pin.unit_id, manifest_checksum=other_revision
        ),
    )
    with pytest.raises(ValueError, match="each unit at most once"):
        params.model_validate({"revisions": [pin, same_unit]})

    foreign = _pin("episode" if domain == "scene" else "scene")
    with pytest.raises(ValueError, match=f"must be {domain} revisions"):
        params.model_validate({"revisions": [pin, foreign]})


def test_a_pin_whose_fields_disagree_is_refused():
    pin = _pin("scene").model_dump()
    pin["manifest_checksum"] = "sha256:" + "c" * 64
    with pytest.raises(ValueError):
        ValidateSceneJobParams.model_validate({"revisions": [pin]})
