"""ScenarioSet curation over pinned sample views, and readiness scoring of
the pinned set (ADR-007 §33.4)."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.jobs.schemas import (
    JobType,
    ScoreScenarioReadinessJobParams,
)
from sceneops_core.scenarios import ScenarioSortKey
from sceneops_worker.derived import DerivedManifestConflictError
from sceneops_worker.derived.resolution import (
    LegacyDerivedRecordError,
    resolve_scenario_set,
)
from sceneops_worker.jobs.scenarios import ScoreScenarioReadinessJobHandler
from sceneops_worker.jobs.scenarios.mine_scenarios import (
    DatasetScopeMismatchError,
    LabelSetPinMismatchError,
)
from tests.derived.flow_support import T0, T1, build_views, import_label_set, mine
from tests.derived_harness import make_harness


async def _setup(tmp_path):
    """Scene A: two labels. Scene B: covered, no objects. Scene C: uncovered."""
    harness = make_harness(tmp_path)
    scenes = {
        name: await harness.register_scene(robot_run_id=run)
        for name, run in (("a", "run-001"), ("b", "run-002"), ("c", "run-003"))
    }
    from tests.derived.labels_support import label_document, write_document
    from sceneops_core.jobs.schemas import ImportLabelsJobParams
    from sceneops_worker.jobs.derived import ImportLabelsJobHandler
    from sceneops_core.labels import LabelSetManifest

    a = label_document(
        "gt",
        run="run-001",
        covered=[T0, T1],
        labels={"a0": (T0, 10.0), "a1": (T0, 20.0)},
    )
    b = label_document("gt", run="run-002", covered=[T0, T1])
    merged = LabelSetManifest.normalized(
        label_set_id="gt",
        provenance=a.provenance,
        coverage=[*a.coverage, *b.coverage],
        labels=list(a.labels),
    )
    uri = await write_document(harness, "gt.json", merged)
    imported = await ImportLabelsJobHandler().run(
        harness.request(JobType.IMPORT_LABELS, ImportLabelsJobParams(document_uri=uri))
    )
    built = await build_views(harness, label_sets=[imported.label_set])
    return harness, scenes, imported.label_set, {v.scene_id: v for v in built.views}


async def test_require_labels_keeps_only_labelled_scenes_and_their_covered_samples(
    tmp_path,
):
    harness, scenes, label_set, views = await _setup(tmp_path)
    result = await mine(harness, views.values(), label_set_id="gt", require_labels=True)
    assert result.selected_scene_ids == [scenes["a"].scene_id]
    assert (result.selected_count, result.rejected_count) == (1, 2)

    resolved = await resolve_scenario_set(harness, result.scenario_set_id)
    member = resolved.manifest.members[0]
    assert member.sample_view == views[scenes["a"].scene_id]
    assert member.label_count == 2
    assert member.sample_ids == ["smp-000000", "smp-000002"]  # the covered samples
    assert resolved.manifest.curation.label_set == label_set
    assert resolved.ref.manifest_checksum == result.scenario_set_checksum

    record = await harness.artifact_record_store.get(
        result.scenario_set_manifest_artifact_id
    )
    assert record.kind == ArtifactKind.SCENARIO_SET_MANIFEST.value
    assert record.scenario_set_id == result.scenario_set_id
    stored = harness.scenario_store.sets[result.scenario_set_id]
    assert stored.manifest_checksum == result.scenario_set_checksum


async def test_label_count_orders_and_max_candidates_truncates(tmp_path):
    harness, scenes, _ls, views = await _setup(tmp_path)
    ordered = await mine(harness, views.values(), label_set_id="gt")
    assert ordered.selected_scene_ids[0] == scenes["a"].scene_id  # most labels first
    assert ordered.selected_count == 3

    truncated = await mine(harness, views.values(), label_set_id="gt", max_candidates=1)
    assert truncated.selected_scene_ids == [scenes["a"].scene_id]
    report = await harness.artifact_store.read_json(truncated.report_uri)
    assert {r["scene_id"]: r["reasons"] for r in report["rejected"]} == {
        scenes["b"].scene_id: ["max_candidates"],
        scenes["c"].scene_id: ["max_candidates"],
    }

    by_scene = await mine(
        harness, views.values(), sort_by=ScenarioSortKey.SCENE_ID, order="asc"
    )
    assert by_scene.selected_scene_ids == sorted(s.scene_id for s in scenes.values())


async def test_count_and_channel_and_readiness_criteria(tmp_path):
    harness, scenes, _ls, views = await _setup(tmp_path)
    harness.add_validation(scenes["a"], status="ready")
    harness.add_validation(scenes["b"], status="warning")
    harness.add_validation(scenes["c"], status="failed", block=True)

    result = await mine(
        harness,
        views.values(),
        readiness=["ready", "warning"],
        sort_by=ScenarioSortKey.SCENE_ID,
        order="asc",
    )
    assert set(result.selected_scene_ids) == {
        scenes["a"].scene_id,
        scenes["b"].scene_id,
    }

    assert (
        await mine(harness, views.values(), label_set_id="gt", min_label_count=3)
    ).selected_count == 0
    assert (
        await mine(harness, views.values(), label_set_id="gt", max_label_count=0)
    ).selected_count == 2
    assert (
        await mine(harness, views.values(), required_channels=["NOPE"])
    ).selected_count == 0
    assert (
        await mine(
            harness, views.values(), required_channels=["CAM_FRONT", "LIDAR_TOP"]
        )
    ).selected_count == 3
    assert (await mine(harness, views.values(), min_sample_count=3)).selected_count == 0


async def test_views_must_pin_the_same_label_set_revision(tmp_path):
    harness, scenes, _ls, views = await _setup(tmp_path)
    other = await import_label_set(harness, "gt", run="run-003", covered=[T0])
    rebuilt = await build_views(
        harness, label_sets=[other], scene_ids=[scenes["c"].scene_id]
    )
    mixed = [views[scenes["a"].scene_id], rebuilt.views[0]]
    with pytest.raises(LabelSetPinMismatchError, match="2 different revisions"):
        await mine(harness, mixed, label_set_id="gt")

    unlabeled = await build_views(harness, scene_ids=[scenes["c"].scene_id])
    with pytest.raises(LabelSetPinMismatchError, match="does not pin"):
        await mine(harness, unlabeled.views, label_set_id="gt")


async def test_views_of_another_dataset_version_are_rejected(tmp_path):
    harness, _scenes, _ls, views = await _setup(tmp_path)
    with pytest.raises(DatasetScopeMismatchError):
        await mine(harness, views.values(), dataset_version="v2")


async def test_a_scenario_set_is_immutable(tmp_path):
    harness, _scenes, _ls, views = await _setup(tmp_path)
    first = await mine(
        harness, views.values(), label_set_id="gt", output_scenario_set_id="scset-fixed"
    )
    again = await mine(
        harness, views.values(), label_set_id="gt", output_scenario_set_id="scset-fixed"
    )
    assert again.scenario_set_checksum == first.scenario_set_checksum
    with pytest.raises(DerivedManifestConflictError, match="immutable"):
        await mine(
            harness,
            views.values(),
            label_set_id="gt",
            require_labels=True,
            output_scenario_set_id="scset-fixed",
        )


async def test_legacy_unpinned_scenario_sets_are_refused(tmp_path):
    from sceneops_core.scenarios import ScenarioSetRecord

    harness = make_harness(tmp_path)
    await harness.scenario_store.upsert(
        ScenarioSetRecord(scenario_set_id="old", scenario_set_uri="file:///old.json")
    )
    with pytest.raises(LegacyDerivedRecordError, match="mine it again"):
        await resolve_scenario_set(harness, "old")


async def test_readiness_scores_the_pinned_members(tmp_path):
    harness, scenes, _ls, views = await _setup(tmp_path)
    harness.add_validation(scenes["a"], status="ready")
    mined = await mine(
        harness,
        views.values(),
        label_set_id="gt",
        required_channels=["CAM_FRONT", "LIDAR_TOP"],
    )

    result = await ScoreScenarioReadinessJobHandler().run(
        harness.request(
            JobType.SCORE_SCENARIO_READINESS,
            ScoreScenarioReadinessJobParams(scenario_set_id=mined.scenario_set_id),
        )
    )
    report = await harness.artifact_store.read_json(result.readiness_report_uri)
    assert report["scenario_set"]["manifest_checksum"] == mined.scenario_set_checksum
    scored = {s["scene_id"]: s for s in report["scenes"]}
    a = scored[scenes["a"].scene_id]
    # labels .30 + validation .25 + channels .20 + density .15 + completeness .10
    assert a["readiness_score"] == 1.0 and a["readiness_bucket"] == "ready"
    assert a["components"]["labels"] == 0.30
    c = scored[scenes["c"].scene_id]
    assert c["components"]["labels"] == 0.0 and c["components"]["validation"] == 0.0
    assert (result.ready_count, result.scored_scene_count) == (1, 3)
    assert result.top_scene_ids == [scenes["a"].scene_id]

    with pytest.raises(ValueError, match="requires scenario_set_id"):
        await ScoreScenarioReadinessJobHandler().run(
            harness.request(
                JobType.SCORE_SCENARIO_READINESS, ScoreScenarioReadinessJobParams()
            )
        )
