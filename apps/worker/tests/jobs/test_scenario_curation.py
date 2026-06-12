"""Unit tests for scenario curation mining and scoring logic.

Tests are pure (no DB, no artifact store) and cover:
- Candidate profile filter defaults
- User param overrides
- Sorting
- Scoring components and determinism
- Bucket thresholds
- Result schema round-trips
"""

from __future__ import annotations

import pytest

from sceneops_core.jobs.schemas.params.scenario import (
    MineScenariosJobParams,
    ScoreScenarioReadinessJobParams,
)
from sceneops_core.jobs.schemas.results.scenario import (
    MineScenariosJobResult,
    ScoreScenarioReadinessJobResult,
)
from sceneops_core.scenarios.schemas.runs import (
    ScenarioMiningRunRecord,
    ScenarioReadinessRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_db.converters.scenarios import scenario_run_record_to_values

from sceneops_worker.jobs.scenarios.mine_scenarios import (
    _get_profile_defaults,
    _passes_filters,
    _SORT_KEYS,
)
from sceneops_worker.jobs.scenarios.score_scenario_readiness import (
    _score_candidate,
    _readiness_bucket,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _candidate(
    scene_id: str = "scene-001",
    *,
    annotation_count: int = 1000,
    sample_count: int = 40,
    frame_count: int = 80,
    has_ground_truth: bool = True,
    channels: list[str] | None = None,
    status: str = "profiled",
) -> dict:
    return {
        "scene_id": scene_id,
        "annotation_count": annotation_count,
        "sample_count": sample_count,
        "frame_count": frame_count,
        "has_ground_truth": has_ground_truth,
        "channels": channels or ["CAM_FRONT", "LIDAR_TOP"],
        "status": status,
        "validation_status": (
            "ready"
            if status in ("validated", "profiled")
            else "blocked"
            if status == "failed"
            else "unknown"
        ),
    }


def _mining_params(**kwargs) -> MineScenariosJobParams:
    return MineScenariosJobParams(
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        **kwargs,
    )


def _check_passes(scene: dict, params: MineScenariosJobParams) -> bool:
    profile = _get_profile_defaults(params.candidate_profile)
    passes, _ = _passes_filters(
        scene_id=scene["scene_id"],
        status=scene["status"],
        annotation_count=scene["annotation_count"],
        sample_count=scene["sample_count"],
        frame_count=scene["frame_count"],
        has_ground_truth=scene["has_ground_truth"],
        channels=scene["channels"],
        params=params,
        profile=profile,
    )
    return passes


# ── profile: detection_ready ──────────────────────────────────────────────────


class TestDetectionReadyProfile:
    def test_gt_profiled_scene_passes(self) -> None:
        scene = _candidate(
            has_ground_truth=True, annotation_count=500, status="profiled"
        )
        params = _mining_params(candidate_profile="detection_ready")
        assert _check_passes(scene, params) is True

    def test_no_gt_rejected(self) -> None:
        scene = _candidate(
            has_ground_truth=False, annotation_count=500, status="profiled"
        )
        params = _mining_params(candidate_profile="detection_ready")
        assert _check_passes(scene, params) is False

    def test_zero_annotation_count_rejected(self) -> None:
        scene = _candidate(has_ground_truth=True, annotation_count=0, status="profiled")
        params = _mining_params(candidate_profile="detection_ready")
        assert _check_passes(scene, params) is False

    def test_unvalidated_status_rejected(self) -> None:
        scene = _candidate(
            has_ground_truth=True, annotation_count=500, status="registered"
        )
        params = _mining_params(candidate_profile="detection_ready")
        assert _check_passes(scene, params) is False

    def test_validation_failed_rejected(self) -> None:
        scene = _candidate(has_ground_truth=True, annotation_count=500, status="failed")
        params = _mining_params(candidate_profile="detection_ready")
        assert _check_passes(scene, params) is False

    def test_validated_status_passes(self) -> None:
        scene = _candidate(
            has_ground_truth=True, annotation_count=500, status="validated"
        )
        params = _mining_params(candidate_profile="detection_ready")
        assert _check_passes(scene, params) is True


# ── profile: no_gt_candidates ─────────────────────────────────────────────────


class TestNoGtCandidatesProfile:
    def test_no_gt_scene_passes(self) -> None:
        scene = _candidate(
            has_ground_truth=False, annotation_count=0, status="registered"
        )
        params = _mining_params(candidate_profile="no_gt_candidates")
        assert _check_passes(scene, params) is True

    def test_gt_scene_rejected(self) -> None:
        scene = _candidate(has_ground_truth=True, annotation_count=500)
        params = _mining_params(candidate_profile="no_gt_candidates")
        assert _check_passes(scene, params) is False


# ── profile: all ──────────────────────────────────────────────────────────────


class TestAllProfile:
    def test_all_scenes_pass_by_default(self) -> None:
        for status in ("registered", "validated", "profiled", "failed"):
            scene = _candidate(
                has_ground_truth=False, annotation_count=0, status=status
            )
            params = _mining_params(candidate_profile="all")
            assert _check_passes(scene, params) is True


# ── user param overrides ──────────────────────────────────────────────────────


class TestUserParamOverrides:
    def test_min_annotation_count_override(self) -> None:
        passing = _candidate(annotation_count=1001)
        failing = _candidate(annotation_count=999)
        params = _mining_params(candidate_profile="all", min_annotation_count=1000)
        assert _check_passes(passing, params) is True
        assert _check_passes(failing, params) is False

    def test_max_annotation_count(self) -> None:
        passing = _candidate(annotation_count=500)
        failing = _candidate(annotation_count=501)
        params = _mining_params(candidate_profile="all", max_annotation_count=500)
        assert _check_passes(passing, params) is True
        assert _check_passes(failing, params) is False

    def test_required_channels_all_present(self) -> None:
        scene = _candidate(channels=["CAM_FRONT", "LIDAR_TOP", "CAM_BACK"])
        params = _mining_params(
            candidate_profile="all",
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
        )
        assert _check_passes(scene, params) is True

    def test_required_channels_missing(self) -> None:
        scene = _candidate(channels=["CAM_FRONT"])
        params = _mining_params(
            candidate_profile="all",
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
        )
        assert _check_passes(scene, params) is False

    def test_has_ground_truth_override_on_all_profile(self) -> None:
        gt_scene = _candidate(has_ground_truth=True)
        no_gt_scene = _candidate(has_ground_truth=False)
        params = _mining_params(candidate_profile="all", has_ground_truth=True)
        assert _check_passes(gt_scene, params) is True
        assert _check_passes(no_gt_scene, params) is False


# ── sorting ───────────────────────────────────────────────────────────────────


class TestSorting:
    def test_sort_by_annotation_count_desc(self) -> None:
        candidates = [
            {"annotation_count": 100, "scene_id": "a"},
            {"annotation_count": 500, "scene_id": "b"},
            {"annotation_count": 200, "scene_id": "c"},
        ]
        key = _SORT_KEYS["annotation_count"]
        sorted_candidates = sorted(candidates, key=key, reverse=True)
        assert sorted_candidates[0]["scene_id"] == "b"
        assert sorted_candidates[-1]["scene_id"] == "a"

    def test_sort_by_scene_id_asc(self) -> None:
        candidates = [
            {"annotation_count": 0, "scene_id": "scene-z"},
            {"annotation_count": 0, "scene_id": "scene-a"},
        ]
        key = _SORT_KEYS["scene_id"]
        sorted_candidates = sorted(candidates, key=key, reverse=False)
        assert sorted_candidates[0]["scene_id"] == "scene-a"


# ── scoring ───────────────────────────────────────────────────────────────────


class TestScoringComponents:
    def _score(
        self,
        candidate: dict,
        *,
        required_channels: list[str] | None = None,
        max_ann: int = 1000,
    ) -> tuple:
        return _score_candidate(
            candidate,
            required_channels=required_channels or [],
            max_annotation_count=max_ann,
        )

    def test_full_score_ideal_candidate(self) -> None:
        candidate = {
            "has_ground_truth": True,
            "annotation_count": 1000,
            "sample_count": 40,
            "frame_count": 80,
            "channels": ["CAM_FRONT", "LIDAR_TOP"],
            "validation_status": "ready",
        }
        score, components, _ = self._score(
            candidate, required_channels=["CAM_FRONT", "LIDAR_TOP"], max_ann=1000
        )
        assert components["gt"] == 0.30
        assert components["validation"] == 0.25
        assert components["channels"] == 0.20
        assert components["density"] == pytest.approx(0.15, abs=0.01)
        assert components["completeness"] == 0.10
        assert score >= 0.98

    def test_no_gt_gives_zero_gt_component(self) -> None:
        candidate = {
            "has_ground_truth": False,
            "annotation_count": 0,
            "sample_count": 10,
            "frame_count": 20,
            "channels": [],
            "validation_status": "unknown",
        }
        _, components, _ = self._score(candidate)
        assert components["gt"] == 0.0

    def test_validation_warning_gives_partial_score(self) -> None:
        candidate = {
            "has_ground_truth": False,
            "annotation_count": 0,
            "sample_count": 10,
            "frame_count": 20,
            "channels": [],
            "validation_status": "warning",
        }
        _, components, _ = self._score(candidate)
        assert components["validation"] == 0.15

    def test_missing_required_channel_partial_credit(self) -> None:
        candidate = {
            "has_ground_truth": False,
            "annotation_count": 0,
            "sample_count": 1,
            "frame_count": 1,
            "channels": ["CAM_FRONT"],
            "validation_status": "unknown",
        }
        _, components, _ = self._score(
            candidate,
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
        )
        assert components["channels"] == 0.10

    def test_missing_all_required_channels(self) -> None:
        candidate = {
            "has_ground_truth": False,
            "annotation_count": 0,
            "sample_count": 1,
            "frame_count": 1,
            "channels": [],
            "validation_status": "unknown",
        }
        _, components, _ = self._score(
            candidate,
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
        )
        assert components["channels"] == 0.0

    def test_scoring_is_deterministic(self) -> None:
        candidate = {
            "has_ground_truth": True,
            "annotation_count": 500,
            "sample_count": 20,
            "frame_count": 40,
            "channels": ["CAM_FRONT"],
            "validation_status": "ready",
        }
        score1, c1, _ = self._score(candidate, max_ann=1000)
        score2, c2, _ = self._score(candidate, max_ann=1000)
        assert score1 == score2
        assert c1 == c2


# ── bucket thresholds ─────────────────────────────────────────────────────────


class TestBucketThresholds:
    def test_ready_threshold(self) -> None:
        assert _readiness_bucket(0.75) == "ready"
        assert _readiness_bucket(1.0) == "ready"
        assert _readiness_bucket(0.8) == "ready"

    def test_warning_threshold(self) -> None:
        assert _readiness_bucket(0.74) == "warning"
        assert _readiness_bucket(0.40) == "warning"
        assert _readiness_bucket(0.50) == "warning"

    def test_blocked_threshold(self) -> None:
        assert _readiness_bucket(0.39) == "blocked"
        assert _readiness_bucket(0.0) == "blocked"


# ── schema round-trips ────────────────────────────────────────────────────────


class TestSchemaRoundTrips:
    def test_mine_scenarios_result_round_trip(self) -> None:
        result = MineScenariosJobResult(
            scenario_set_id="scset-abc",
            scenario_set_uri="file:///runs/mining/run-001/candidates.json",
            report_uri="file:///runs/mining/run-001/report.json",
            mining_run_id="mining-001",
            candidate_count=10,
            selected_count=10,
            rejected_count=5,
            selected_scene_ids=["scene-0001", "scene-0002"],
            summary={"candidate_profile": "detection_ready"},
        )
        data = result.model_dump()
        assert data["scenario_set_id"] == "scset-abc"
        assert data["mining_run_id"] == "mining-001"
        assert data["selected_scene_ids"] == ["scene-0001", "scene-0002"]
        assert data["candidate_count"] == 10
        assert data["rejected_count"] == 5

    def test_score_result_round_trip(self) -> None:
        result = ScoreScenarioReadinessJobResult(
            scenario_set_id="scset-abc",
            readiness_report_uri="file:///runs/readiness/run-001/report.json",
            readiness_run_id="readiness-001",
            scored_scene_count=10,
            average_score=0.88,
            ready_count=8,
            warning_count=2,
            blocked_count=0,
            top_scene_ids=["scene-0001"],
            summary={"score_profile": "evaluation_readiness"},
        )
        data = result.model_dump()
        assert data["readiness_run_id"] == "readiness-001"
        assert data["scored_scene_count"] == 10
        assert data["warning_count"] == 2
        assert data["top_scene_ids"] == ["scene-0001"]
        assert data["average_score"] == pytest.approx(0.88)

    def test_mine_params_defaults(self) -> None:
        params = MineScenariosJobParams(dataset_id="ds", dataset_version="v1")
        assert params.candidate_profile == "detection_ready"
        assert params.sort_by == "annotation_count"
        assert params.order == "desc"
        assert params.max_candidates == 50

    def test_score_params_defaults(self) -> None:
        params = ScoreScenarioReadinessJobParams()
        assert params.score_profile == "evaluation_readiness"
        assert params.required_channels == []


# ── regression: summary field alignment with DB converter ────────────────────


class TestRunRecordSummaryRoundTrip:
    """Regression tests for AttributeError: 'ScenarioMiningRunRecord' has no attribute 'summary'.

    The DB converter expects record.summary; run records must expose that field.
    """

    def test_mining_run_record_has_summary_field(self) -> None:
        record = ScenarioMiningRunRecord(
            run_id="mining-001",
            type=RunType.SCENARIO_MINING,
            status=RunStatus.SUCCEEDED,
            summary={"candidate_profile": "detection_ready"},
        )
        assert record.summary == {"candidate_profile": "detection_ready"}

    def test_mining_run_record_summary_round_trips_through_converter(self) -> None:
        summary = {
            "candidate_profile": "detection_ready",
            "predicate": {"has_ground_truth": True, "min_annotation_count": 1},
            "counts": {
                "input_scene_count": 30,
                "selected_count": 10,
                "rejected_count": 20,
            },
            "selection": {"selected_scene_ids": ["scene-0103"]},
        }
        record = ScenarioMiningRunRecord(
            run_id="mining-001",
            type=RunType.SCENARIO_MINING,
            status=RunStatus.SUCCEEDED,
            candidate_count=10,
            selected_count=10,
            rejected_count=20,
            summary=summary,
        )
        values = scenario_run_record_to_values(record)
        assert values["summary"] == summary
        assert values["candidate_count"] == 10
        assert values["selected_count"] == 10
        assert values["rejected_count"] == 20

    def test_readiness_run_record_has_summary_field(self) -> None:
        record = ScenarioReadinessRunRecord(
            run_id="readiness-001",
            type=RunType.SCENARIO_READINESS,
            status=RunStatus.SUCCEEDED,
            summary={"score_profile": "evaluation_readiness"},
        )
        assert record.summary == {"score_profile": "evaluation_readiness"}

    def test_readiness_run_record_summary_round_trips_through_converter(self) -> None:
        summary = {
            "score_profile": "evaluation_readiness",
            "buckets": {"ready_count": 8, "warning_count": 2, "blocked_count": 0},
            "top_scene_ids": ["scene-0103", "scene-0553"],
        }
        record = ScenarioReadinessRunRecord(
            run_id="readiness-001",
            type=RunType.SCENARIO_READINESS,
            status=RunStatus.SUCCEEDED,
            ready_count=8,
            warning_count=2,
            blocked_count=0,
            average_score=0.88,
            summary=summary,
        )
        values = scenario_run_record_to_values(record)
        assert values["summary"] == summary
        assert values["ready_count"] == 8
        assert values["warning_count"] == 2
        assert values["average_score"] == pytest.approx(0.88)

    def test_mining_run_record_model_copy_with_summary(self) -> None:
        """Regression: model_copy with summary= must not raise AttributeError."""
        initial = ScenarioMiningRunRecord(
            run_id="mining-001",
            type=RunType.SCENARIO_MINING,
            status=RunStatus.RUNNING,
        )
        updated = initial.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "summary": {"candidate_profile": "detection_ready", "predicate": {}},
                "candidate_count": 5,
            }
        )
        assert updated.status == RunStatus.SUCCEEDED
        assert updated.summary["candidate_profile"] == "detection_ready"
        assert updated.candidate_count == 5

    def test_readiness_run_record_model_copy_with_summary(self) -> None:
        """Regression: model_copy with summary= must not raise AttributeError."""
        initial = ScenarioReadinessRunRecord(
            run_id="readiness-001",
            type=RunType.SCENARIO_READINESS,
            status=RunStatus.RUNNING,
        )
        updated = initial.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "summary": {"score_profile": "evaluation_readiness", "buckets": {}},
                "ready_count": 3,
            }
        )
        assert updated.status == RunStatus.SUCCEEDED
        assert updated.summary["score_profile"] == "evaluation_readiness"
        assert updated.ready_count == 3
