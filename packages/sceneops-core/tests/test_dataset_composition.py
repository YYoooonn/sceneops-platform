"""DatasetVersion composition rules (ADR-015 §3, §7; MS-6, MS-8): the
composition checksum, member admission, summaries, selectors and the
COMPOSE_DATASET_VERSION params."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from sceneops_core.datasets.composition import (
    COMPOSITION_SCHEMA_V1,
    DuplicateMemberUnitError,
    EmptyCompositionError,
    composition_checksum,
    composition_members,
    summarize_composition,
)
from sceneops_core.datasets.schemas import RevisionSelector, RevisionSourceClause
from sceneops_core.episodes.schemas import episode_id_for, project_episode_revision
from sceneops_core.episodes.testing import episode_manifest, state
from sceneops_core.jobs.schemas import (
    ComposeDatasetVersionJobParams,
    ComposeDatasetVersionJobResult,
    JobType,
    create_initial_job_steps,
    parse_job_params,
)
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_core.scenes.schemas import project_scene_revision, scene_id_for
from sceneops_core.scenes.testing import build_scene_manifest, recording_source

FINGERPRINT = "sha256:" + "f" * 64


def _scene(unit_key: str = "segment-0000", **kwargs):
    source = recording_source(unit_key=unit_key)
    manifest = build_scene_manifest(source=source, **kwargs)
    checksum = manifest.checksum()
    return project_scene_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id_for(source), manifest_checksum=checksum
        ),
        manifest_checksum=checksum,
    )


def _episode(unit_key: str = "recording", **kwargs):
    manifest = episode_manifest([state("/odom", 1, x=1.0)], unit_key=unit_key, **kwargs)
    checksum = manifest.checksum()
    return project_episode_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=episode_id_for(manifest.lineage.source), manifest_checksum=checksum
        ),
        manifest_checksum=checksum,
    )


# --- composition checksum -----------------------------------------------------------


def test_checksum_is_the_documented_formula_over_sorted_pins():
    """Recomputed here from the formula in the module docstring, not from
    the implementation's own helpers."""
    pins = [_scene("b").pin(), _episode().pin(), _scene("a").pin()]
    triples = sorted([p.domain, p.unit_id, p.manifest_checksum] for p in pins)
    document = json.dumps(
        {"composition_schema": COMPOSITION_SCHEMA_V1, "members": triples},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    assert COMPOSITION_SCHEMA_V1 == "sceneops.dataset_version_composition/v1"
    assert (
        composition_checksum(pins) == "sha256:" + hashlib.sha256(document).hexdigest()
    )


def test_checksum_is_independent_of_input_order():
    pins = [_scene("a").pin(), _scene("b").pin(), _episode().pin()]
    assert composition_checksum(pins) == composition_checksum(reversed(pins))
    assert [m.unit_id for m in composition_members(reversed(pins))] == [
        m.unit_id for m in composition_members(pins)
    ]


def test_checksum_changes_with_any_member():
    a, b = _scene("a").pin(), _scene("b").pin()
    other_revision = _scene("a", build_config={"channels": ["CAM_FRONT"]}).pin()
    assert other_revision.unit_id == a.unit_id
    checksums = {
        composition_checksum([a]),
        composition_checksum([a, b]),
        composition_checksum([other_revision, b]),
    }
    assert len(checksums) == 3


def test_a_composition_has_at_least_one_member():
    with pytest.raises(EmptyCompositionError):
        composition_members([])
    with pytest.raises(EmptyCompositionError):
        composition_checksum([])


@pytest.mark.parametrize("same_revision", [True, False])
def test_a_composition_pins_one_revision_per_unit(same_revision):
    one = _scene("a")
    two = (
        one if same_revision else _scene("a", build_config={"channels": ["CAM_FRONT"]})
    )
    with pytest.raises(DuplicateMemberUnitError, match=one.scene_id):
        composition_members([one.pin(), two.pin()])


# --- summaries -----------------------------------------------------------------------


def test_summaries_aggregate_member_projections_per_domain():
    a = _scene("a")
    b = _scene("b", camera_channel="CAM_BACK", keyframe_timestamps_ns=(1_000,))
    episode = _episode()

    scene, episodes = summarize_composition(scenes=[a, b], episodes=[episode])

    assert scene.scene_count == 2
    assert scene.keyframe_count == a.keyframe_count + b.keyframe_count
    assert scene.observation_count == a.observation_count + b.observation_count
    assert scene.observed_channels == sorted(
        set(a.observed_channels) | set(b.observed_channels)
    )
    assert episodes.episode_count == 1
    assert summarize_composition(scenes=[], episodes=[episode])[0] is None
    assert summarize_composition(scenes=[a], episodes=[])[1] is None


# --- selector ------------------------------------------------------------------------


def _clause(domain="scene", run="run-1", fingerprint=FINGERPRINT):
    return {"domain": domain, "robot_run_id": run, "producer_fingerprint": fingerprint}


def test_selector_clauses_are_canonical_and_unique():
    selector = RevisionSelector.model_validate(
        {"sources": [_clause("scene", "run-2"), _clause("episode", "run-1")]}
    )
    assert [(c.domain, c.robot_run_id) for c in selector.sources] == [
        ("episode", "run-1"),
        ("scene", "run-2"),
    ]
    with pytest.raises(ValidationError, match="duplicate"):
        RevisionSelector.model_validate({"sources": [_clause(), _clause()]})
    with pytest.raises(ValidationError):
        RevisionSelector.model_validate({"sources": []})


@pytest.mark.parametrize(
    "clause",
    [
        _clause(domain="robot_run"),
        _clause(run=""),
        _clause(fingerprint="abc"),
        {**_clause(), "dataset_version": "v1"},
    ],
)
def test_a_selector_clause_is_closed(clause):
    with pytest.raises(ValidationError):
        RevisionSourceClause.model_validate(clause)


# --- job params ----------------------------------------------------------------------


def _params(**overrides):
    return {"dataset_id": "ds", "dataset_version": "v1", **overrides}


def test_params_take_exactly_one_member_source():
    pin = _scene().pin().model_dump()
    selector = {"sources": [_clause()]}
    for invalid in (_params(), _params(pins=[pin], selector=selector)):
        with pytest.raises(ValidationError, match="exactly one of pins or selector"):
            parse_job_params(JobType.COMPOSE_DATASET_VERSION, invalid)
    assert parse_job_params(
        JobType.COMPOSE_DATASET_VERSION, _params(selector=selector)
    ).selector == RevisionSelector.model_validate(selector)


def test_params_pins_are_canonical_and_admissible():
    a, b = _scene("a").pin(), _scene("b").pin()
    forward = ComposeDatasetVersionJobParams.model_validate(
        _params(pins=[a.model_dump(), b.model_dump()])
    )
    backward = ComposeDatasetVersionJobParams.model_validate(
        _params(pins=[b.model_dump(), a.model_dump()])
    )
    # Same pins, same params, hence the same execution key.
    assert forward.model_dump() == backward.model_dump()

    for invalid in ([], [a.model_dump(), a.model_dump()]):
        with pytest.raises(ValidationError):
            ComposeDatasetVersionJobParams.model_validate(_params(pins=invalid))
    tampered = {**a.model_dump(), "manifest_checksum": b.manifest_checksum}
    with pytest.raises(ValidationError):
        ComposeDatasetVersionJobParams.model_validate(_params(pins=[tampered]))
    for missing in ("dataset_id", "dataset_version"):
        incomplete = _params(pins=[a.model_dump()])
        del incomplete[missing]
        with pytest.raises(ValidationError):
            ComposeDatasetVersionJobParams.model_validate(incomplete)


def test_job_contract_is_registered():
    assert JobType.COMPOSE_DATASET_VERSION.value == "compose_dataset_version"
    assert [
        s.job_step_id for s in create_initial_job_steps(JobType.COMPOSE_DATASET_VERSION)
    ] == [
        "resolve_pins",
        "commit_composition",
    ]
    result = ComposeDatasetVersionJobResult(
        dataset_id="ds",
        dataset_version="v1",
        composition_checksum="sha256:" + "c" * 64,
        created=False,
        member_count=1,
    )
    assert result.scene_count == result.episode_count == 0
